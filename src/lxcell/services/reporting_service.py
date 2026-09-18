"""Reporting calculations for Phase 1 accounting data."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from lxcell.db.models import BudgetLine, Transaction
from lxcell.enums.core_enums import (
    CategoryType,
    Direction,
    OwnershipType,
    SharedExpenseStatus,
    TransactionReviewStatus,
    TransactionType,
)
from lxcell.repositories import AccountingRepository


class ReportAmountBasis(StrEnum):
    """Controls whether reports use gross or effective personal amounts."""

    GROSS = "gross"
    PERSONAL = "personal"


@dataclass(frozen=True)
class CashflowSummary:
    """Cashflow totals for a date range, stored in minor currency units."""

    inflow_minor: int
    outflow_minor: int
    neutral_minor: int
    net_minor: int


@dataclass(frozen=True)
class CategoryTotal:
    """Transaction total grouped by category."""

    category_id: int | None
    category_name: str | None
    category_type: CategoryType | None
    amount_minor: int


@dataclass(frozen=True)
class BudgetActualLine:
    """Budget-vs-actual result for one budget line."""

    category_id: int
    category_name: str
    category_type: CategoryType
    planned_amount_minor: int
    actual_amount_minor: int
    remaining_minor: int


@dataclass(frozen=True)
class BudgetActualSummary:
    """Budget-vs-actual result for one budget and date range."""

    budget_id: int
    budget_name: str
    start_date: date
    end_date: date
    planned_amount_minor: int
    actual_amount_minor: int
    remaining_minor: int
    lines: tuple[BudgetActualLine, ...]
    unbudgeted_actual_amount_minor: int
    unbudgeted_lines: tuple[BudgetActualLine, ...]
    uncategorized_actual_amount_minor: int
    uncategorized_transaction_count: int


class ReportingService:
    """Service layer for Phase 1 reporting calculations."""

    def __init__(self, repository: AccountingRepository) -> None:
        self.repository = repository

    def summarize_cashflow(
        self,
        *,
        user_profile_id: int,
        start_date: date,
        end_date: date,
        include_transfers: bool = False,
        amount_basis: ReportAmountBasis = ReportAmountBasis.GROSS,
    ) -> CashflowSummary:
        amount_basis = ReportAmountBasis(amount_basis)
        inflow_minor = 0
        outflow_minor = 0
        neutral_minor = 0

        for transaction in self._reportable_transactions(
            user_profile_id=user_profile_id,
            start_date=start_date,
            end_date=end_date,
            include_transfers=include_transfers,
        ):
            report_amount_minor = self._report_amount_minor(
                transaction,
                amount_basis=amount_basis,
            )
            cashflow_bucket = self._cashflow_bucket(transaction)
            if cashflow_bucket == Direction.INFLOW:
                inflow_minor += report_amount_minor
            elif cashflow_bucket == Direction.OUTFLOW:
                outflow_minor += report_amount_minor
            elif cashflow_bucket == Direction.NEUTRAL:
                neutral_minor += report_amount_minor

        return CashflowSummary(
            inflow_minor=inflow_minor,
            outflow_minor=outflow_minor,
            neutral_minor=neutral_minor,
            net_minor=inflow_minor - outflow_minor,
        )

    def summarize_by_category(
        self,
        *,
        user_profile_id: int,
        start_date: date,
        end_date: date,
        include_transfers: bool = False,
        amount_basis: ReportAmountBasis = ReportAmountBasis.GROSS,
    ) -> list[CategoryTotal]:
        amount_basis = ReportAmountBasis(amount_basis)
        totals: dict[int | None, CategoryTotal] = {}

        for transaction in self._reportable_transactions(
            user_profile_id=user_profile_id,
            start_date=start_date,
            end_date=end_date,
            include_transfers=include_transfers,
        ):
            category_id = transaction.category_id
            existing_total = totals.get(category_id)
            amount_minor = self._signed_amount_minor(
                transaction,
                amount_basis=amount_basis,
            )
            if existing_total is None:
                totals[category_id] = CategoryTotal(
                    category_id=category_id,
                    category_name=(
                        transaction.category.name
                        if transaction.category is not None
                        else None
                    ),
                    category_type=(
                        transaction.category.category_type
                        if transaction.category is not None
                        else None
                    ),
                    amount_minor=amount_minor,
                )
            else:
                totals[category_id] = CategoryTotal(
                    category_id=existing_total.category_id,
                    category_name=existing_total.category_name,
                    category_type=existing_total.category_type,
                    amount_minor=existing_total.amount_minor + amount_minor,
                )

        return sorted(
            totals.values(),
            key=lambda total: (
                total.category_name is None,
                total.category_name or "",
                total.category_id or 0,
            ),
        )

    def summarize_budget_actuals(
        self,
        *,
        user_profile_id: int,
        budget_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        include_transfers: bool = False,
        amount_basis: ReportAmountBasis = ReportAmountBasis.GROSS,
    ) -> BudgetActualSummary:
        amount_basis = ReportAmountBasis(amount_basis)
        budget = self.repository.get_budget(
            budget_id=budget_id,
            user_profile_id=user_profile_id,
        )
        if budget is None:
            raise ValueError("Budget was not found for the user profile.")
        if not budget.is_active:
            raise ValueError("Budget must be active for Phase 1 reporting.")

        report_start_date = start_date or budget.start_date
        report_end_date = end_date or budget.end_date
        category_total_list = self.summarize_by_category(
            user_profile_id=user_profile_id,
            start_date=report_start_date,
            end_date=report_end_date,
            include_transfers=include_transfers,
            amount_basis=amount_basis,
        )
        category_totals = {
            total.category_id: total.amount_minor
            for total in category_total_list
        }

        lines = tuple(
            self._budget_actual_line(
                budget_line=budget_line,
                signed_actual_minor=category_totals.get(
                    budget_line.category_id, 0
                ),
            )
            for budget_line in sorted(
                budget.budget_lines,
                key=lambda line: (
                    line.category.display_order,
                    line.category.name,
                    line.id,
                ),
            )
        )
        planned_amount_minor = sum(line.planned_amount_minor for line in lines)
        actual_amount_minor = sum(line.actual_amount_minor for line in lines)
        budgeted_category_ids = {line.category_id for line in lines}
        unbudgeted_lines = tuple(
            self._unbudgeted_actual_line(category_total=category_total)
            for category_total in self._unbudgeted_category_totals(
                category_totals=category_total_list,
                budgeted_category_ids=budgeted_category_ids,
            )
        )
        unbudgeted_actual_amount_minor = sum(
            line.actual_amount_minor for line in unbudgeted_lines
        )
        uncategorized_transactions = self._uncategorized_transactions(
            user_profile_id=user_profile_id,
            start_date=report_start_date,
            end_date=report_end_date,
            include_transfers=include_transfers,
        )
        uncategorized_actual_amount_minor = sum(
            self._report_amount_minor(transaction, amount_basis=amount_basis)
            for transaction in uncategorized_transactions
        )

        return BudgetActualSummary(
            budget_id=budget.id,
            budget_name=budget.name,
            start_date=report_start_date,
            end_date=report_end_date,
            planned_amount_minor=planned_amount_minor,
            actual_amount_minor=actual_amount_minor,
            remaining_minor=planned_amount_minor - actual_amount_minor,
            lines=lines,
            unbudgeted_actual_amount_minor=unbudgeted_actual_amount_minor,
            unbudgeted_lines=unbudgeted_lines,
            uncategorized_actual_amount_minor=uncategorized_actual_amount_minor,
            uncategorized_transaction_count=len(uncategorized_transactions),
        )

    def _reportable_transactions(
        self,
        *,
        user_profile_id: int,
        start_date: date,
        end_date: date,
        include_transfers: bool,
    ) -> list[Transaction]:
        transactions = self.repository.list_transactions(
            user_profile_id=user_profile_id,
            start_date=start_date,
            end_date=end_date,
        )
        return [
            transaction
            for transaction in transactions
            if self._is_reportable(
                transaction, include_transfers=include_transfers
            )
        ]

    @staticmethod
    def _is_reportable(
        transaction: Transaction, *, include_transfers: bool
    ) -> bool:
        if transaction.review_status == TransactionReviewStatus.IGNORED:
            return False
        if (
            not include_transfers
            and transaction.transaction_type == TransactionType.TRANSFER
        ):
            return False
        return True

    def _signed_amount_minor(
        self,
        transaction: Transaction,
        *,
        amount_basis: ReportAmountBasis = ReportAmountBasis.GROSS,
    ) -> int:
        if transaction.transaction_type == TransactionType.ADJUSTMENT:
            return 0
        report_amount_minor = self._report_amount_minor(
            transaction,
            amount_basis=amount_basis,
        )
        if transaction.direction == Direction.INFLOW:
            return report_amount_minor
        if transaction.direction == Direction.OUTFLOW:
            return -report_amount_minor
        return 0

    @staticmethod
    def _cashflow_bucket(transaction: Transaction) -> Direction:
        if transaction.transaction_type == TransactionType.ADJUSTMENT:
            return Direction.NEUTRAL
        return transaction.direction

    @staticmethod
    def _report_amount_minor(
        transaction: Transaction,
        *,
        amount_basis: ReportAmountBasis,
    ) -> int:
        allocation = transaction.shared_expense_allocation
        if (
            amount_basis == ReportAmountBasis.PERSONAL
            and transaction.direction == Direction.OUTFLOW
            and allocation is not None
            and allocation.status != SharedExpenseStatus.WAIVED
        ):
            return allocation.personal_share_minor
        if (
            amount_basis == ReportAmountBasis.PERSONAL
            and transaction.direction == Direction.OUTFLOW
            and transaction.transaction_type
            in {
                TransactionType.EXPENSE,
                TransactionType.FEE,
                TransactionType.TAX,
            }
            and transaction.account is not None
            and transaction.account.ownership_type == OwnershipType.SHARED
            and transaction.account.personal_reporting_share_basis_points is not None
        ):
            return personal_share_amount_minor(
                transaction.amount_minor,
                transaction.account.personal_reporting_share_basis_points,
            )
        return transaction.amount_minor

    def _budget_actual_line(
        self, *, budget_line: BudgetLine, signed_actual_minor: int
    ) -> BudgetActualLine:
        category_type = budget_line.category.category_type
        actual_amount_minor = self._budget_actual_amount_minor(
            category_type=category_type,
            signed_actual_minor=signed_actual_minor,
        )
        return BudgetActualLine(
            category_id=budget_line.category_id,
            category_name=budget_line.category.name,
            category_type=category_type,
            planned_amount_minor=budget_line.amount_minor,
            actual_amount_minor=actual_amount_minor,
            remaining_minor=budget_line.amount_minor - actual_amount_minor,
        )

    @staticmethod
    def _budget_actual_amount_minor(
        *, category_type: CategoryType, signed_actual_minor: int
    ) -> int:
        if category_type == CategoryType.INCOME:
            return signed_actual_minor
        return -signed_actual_minor

    def _unbudgeted_category_totals(
        self,
        *,
        category_totals: list[CategoryTotal],
        budgeted_category_ids: set[int],
    ) -> list[CategoryTotal]:
        return [
            category_total
            for category_total in category_totals
            if category_total.category_id not in budgeted_category_ids
            and category_total.category_id is not None
        ]

    def _unbudgeted_actual_line(
        self, *, category_total: CategoryTotal
    ) -> BudgetActualLine:
        if category_total.category_type is None:
            raise ValueError("Unbudgeted category totals require category_type.")
        if category_total.category_name is None:
            raise ValueError("Unbudgeted category totals require category_name.")
        actual_amount_minor = self._budget_actual_amount_minor(
            category_type=category_total.category_type,
            signed_actual_minor=category_total.amount_minor,
        )
        return BudgetActualLine(
            category_id=category_total.category_id,
            category_name=category_total.category_name,
            category_type=category_total.category_type,
            planned_amount_minor=0,
            actual_amount_minor=actual_amount_minor,
            remaining_minor=-actual_amount_minor,
        )

    def _uncategorized_transactions(
        self,
        *,
        user_profile_id: int,
        start_date: date,
        end_date: date,
        include_transfers: bool,
    ) -> list[Transaction]:
        return [
            transaction
            for transaction in self._reportable_transactions(
                user_profile_id=user_profile_id,
                start_date=start_date,
                end_date=end_date,
                include_transfers=include_transfers,
            )
            if transaction.category_id is None
        ]


def personal_share_amount_minor(amount_minor: int, share_basis_points: int) -> int:
    """Return the rounded personal amount for an account-level share policy."""
    if share_basis_points < 0 or share_basis_points > 10000:
        raise ValueError("Personal share basis points must be between 0 and 10000.")
    return (amount_minor * share_basis_points + 5000) // 10000


__all__ = [
    "BudgetActualLine",
    "BudgetActualSummary",
    "CashflowSummary",
    "CategoryTotal",
    "ReportAmountBasis",
    "ReportingService",
    "personal_share_amount_minor",
]
