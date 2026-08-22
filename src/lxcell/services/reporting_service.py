"""Reporting calculations for Phase 1 accounting data."""

from dataclasses import dataclass
from datetime import date

from lxcell.db.models import Transaction
from lxcell.enums.core_enums import (
    CategoryType,
    Direction,
    TransactionReviewStatus,
    TransactionType,
)
from lxcell.repositories import AccountingRepository


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
    ) -> CashflowSummary:
        inflow_minor = 0
        outflow_minor = 0
        neutral_minor = 0

        for transaction in self._reportable_transactions(
            user_profile_id=user_profile_id,
            start_date=start_date,
            end_date=end_date,
            include_transfers=include_transfers,
        ):
            if transaction.direction == Direction.INFLOW:
                inflow_minor += transaction.amount_minor
            elif transaction.direction == Direction.OUTFLOW:
                outflow_minor += transaction.amount_minor
            elif transaction.direction == Direction.NEUTRAL:
                neutral_minor += transaction.amount_minor

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
    ) -> list[CategoryTotal]:
        totals: dict[int | None, CategoryTotal] = {}

        for transaction in self._reportable_transactions(
            user_profile_id=user_profile_id,
            start_date=start_date,
            end_date=end_date,
            include_transfers=include_transfers,
        ):
            category_id = transaction.category_id
            existing_total = totals.get(category_id)
            amount_minor = self._signed_amount_minor(transaction)
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

    @staticmethod
    def _signed_amount_minor(transaction: Transaction) -> int:
        if transaction.direction == Direction.INFLOW:
            return transaction.amount_minor
        if transaction.direction == Direction.OUTFLOW:
            return -transaction.amount_minor
        return 0


__all__ = ["CashflowSummary", "CategoryTotal", "ReportingService"]
