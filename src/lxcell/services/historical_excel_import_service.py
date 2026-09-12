"""Confirmed historical Excel import workflow."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from lxcell.db.models import (
    Account,
    Category,
    CategoryMapping,
    ImportBatch,
    ImportedTransactionSource,
)
from lxcell.enums.core_enums import (
    AccountType,
    CategoryType,
    CategoryMappingStatus,
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    Direction,
    ImportAction,
    ImportSourceSystem,
    ImportStatus,
    OwnershipType,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.importers import HistoricalExcelPreview, HistoricalExcelTransactionCandidate
from lxcell.repositories import AccountingRepository
from lxcell.services.accounting_service import protected_transaction_dates

HISTORICAL_EXCEL_ACCOUNT_NAME = "Excel histórico"
HISTORICAL_EXCEL_DECIDED_BY_DEFAULT = "system"


@dataclass(frozen=True)
class HistoricalExcelImportResult:
    """Summary of a confirmed historical Excel import."""

    import_batch_id: int
    account_id: int
    transaction_count: int
    created_category_count: int
    reused_category_count: int
    mapped_category_count: int = 0


class HistoricalExcelImportService:
    """Service for writing reviewed historical Excel previews to the database."""

    def __init__(self, repository: AccountingRepository) -> None:
        self.repository = repository

    def confirm_import(
        self,
        *,
        user_profile_id: int,
        preview: HistoricalExcelPreview,
        confirmed_by: str,
        user_confirmed: bool,
        category_id_overrides_by_source_name: dict[str, int] | None = None,
        allow_locked_period_override: bool = False,
    ) -> HistoricalExcelImportResult:
        if not user_confirmed:
            raise ValueError("Historical Excel import requires explicit confirmation.")
        if not confirmed_by:
            raise ValueError("Historical Excel import requires confirmed_by.")

        blockers = historical_import_blockers(preview)
        if blockers:
            raise ValueError(" ".join(blockers))

        duplicate_batch = self._get_completed_import_batch_by_file_hash(
            user_profile_id=user_profile_id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_hash=preview.source_file_hash,
        )
        if duplicate_batch is not None:
            raise ValueError("This historical Excel file was already imported.")

        normalized_hashes = candidate_normalized_hashes(
            user_profile_id=user_profile_id,
            preview=preview,
        )
        duplicate_hashes = duplicated_values(normalized_hashes)
        if duplicate_hashes:
            raise ValueError(
                "Historical Excel preview contains duplicate normalized source rows."
            )
        for normalized_hash in normalized_hashes.values():
            existing_source = self._get_imported_source_by_normalized_hash(
                user_profile_id=user_profile_id,
                source_system=ImportSourceSystem.EXCEL_HISTORICAL,
                normalized_hash=normalized_hash,
            )
            if existing_source is not None:
                raise ValueError(
                    "Historical Excel import overlaps with an already imported source row."
                )

        user_profile = self.repository.get_user_profile(user_profile_id)
        if user_profile is None:
            raise ValueError("User profile was not found.")
        if not allow_locked_period_override and protected_transaction_dates(
            user_profile,
            [candidate.transaction_date for candidate in preview.candidates],
        ):
            raise ValueError(
                "La importacion contiene fechas en un periodo protegido. "
                "Confirma el permiso adicional para continuar."
            )

        account = self._get_or_create_historical_account(
            user_profile_id=user_profile_id,
            currency=user_profile.default_currency,
        )
        categories = self.repository.list_categories(user_profile_id, include_inactive=True)
        category_plan = build_category_plan(
            categories,
            preview,
            category_id_overrides_by_source_name=(
                category_id_overrides_by_source_name or {}
            ),
        )
        category_conflicts = [
            row for row in category_plan if row.action == "conflict"
        ]
        if category_conflicts:
            raise ValueError("Historical Excel import has category conflicts.")

        categories_by_source_name = self._resolve_categories(
            user_profile_id=user_profile_id,
            category_plan=category_plan,
        )
        self.repository.session.flush()

        import_batch = self.repository.add_import_batch(
            user_profile_id=user_profile_id,
            source_system=ImportSourceSystem.EXCEL_HISTORICAL,
            source_file_name=preview.source_file_name,
            source_file_hash=preview.source_file_hash,
            import_status=ImportStatus.PENDING,
            imported_by=confirmed_by,
            notes="Confirmed historical Excel import.",
        )
        self.repository.session.flush()

        self._record_category_mappings(
            user_profile_id=user_profile_id,
            preview=preview,
            import_batch=import_batch,
            category_plan=category_plan,
            categories_by_source_name=categories_by_source_name,
        )
        self.repository.session.flush()

        for candidate in preview.candidates:
            category = categories_by_source_name[candidate.source_category_name]
            normalized_hash = normalized_hashes[candidate_key(candidate)]
            transaction_type = transaction_type_for_candidate(candidate)
            transaction = self.repository.add_transaction(
                user_profile_id=user_profile_id,
                account_id=account.id,
                transaction_date=candidate.transaction_date,
                description_clean=description_clean_for_candidate(candidate),
                description_raw=candidate.description_raw,
                category_id=category.id,
                amount_minor=candidate.amount_minor,
                currency=user_profile.default_currency,
                direction=candidate.direction,
                transaction_type=transaction_type,
                review_status=TransactionReviewStatus.USER_CONFIRMED,
                source_type=TransactionSourceType.EXCEL_IMPORT,
                source_id=normalized_hash,
            )
            self.repository.session.flush()
            self.repository.add_imported_transaction_source(
                import_batch_id=import_batch.id,
                import_action=ImportAction.CREATED_TRANSACTION,
                row_number_source=candidate.row_number_source,
                record_id_source=record_id_source(preview, candidate),
                date_raw=candidate.transaction_date.isoformat(),
                description_raw=candidate.description_raw,
                amount_raw=candidate.amount_raw,
                currency_raw=user_profile.default_currency,
                payload_raw_json=payload_raw_json(preview, candidate),
                normalized_hash=normalized_hash,
                created_transaction_id=transaction.id,
            )
            self.repository.add_classification_decision(
                transaction_id=transaction.id,
                category_id=category.id,
                transaction_type=transaction_type,
                decision_source=ClassificationDecisionSource.HISTORICAL_MATCH,
                decision_status=ClassificationDecisionStatus.ACCEPTED,
                decided_by=confirmed_by or HISTORICAL_EXCEL_DECIDED_BY_DEFAULT,
                notes="Historical Excel import.",
            )

        import_batch.import_status = ImportStatus.COMPLETED
        self.repository.session.flush()
        return HistoricalExcelImportResult(
            import_batch_id=import_batch.id,
            account_id=account.id,
            transaction_count=len(preview.candidates),
            created_category_count=sum(1 for row in category_plan if row.action == "create"),
            reused_category_count=sum(1 for row in category_plan if row.action == "reuse"),
            mapped_category_count=sum(1 for row in category_plan if row.action == "map"),
        )

    def _record_category_mappings(
        self,
        *,
        user_profile_id: int,
        preview: HistoricalExcelPreview,
        import_batch: ImportBatch,
        category_plan: list["HistoricalCategoryPlanRow"],
        categories_by_source_name: dict[str, Category],
    ) -> None:
        source_column_kinds = source_category_kinds_by_name(preview)
        for row in category_plan:
            if row.action == "conflict":
                continue
            category = categories_by_source_name[row.source_category_name]
            existing_mapping = self._get_category_mapping_for_source(
                user_profile_id=user_profile_id,
                source_system=ImportSourceSystem.EXCEL_HISTORICAL,
                source_file_hash=preview.source_file_hash,
                source_category_key=row.source_category_key,
            )
            if existing_mapping is not None:
                if existing_mapping.target_category_id != category.id:
                    raise ValueError(
                        "Historical Excel source category already has a different mapping."
                    )
                existing_mapping.created_from_import_batch_id = import_batch.id
                continue
            self._add_category_mapping(
                user_profile_id=user_profile_id,
                source_system=ImportSourceSystem.EXCEL_HISTORICAL,
                source_file_hash=preview.source_file_hash,
                source_file_name=preview.source_file_name,
                source_category_name=row.source_category_name,
                source_category_key=row.source_category_key,
                source_column_kind=source_column_kinds.get(
                    row.source_category_name,
                    "expense",
                ),
                target_category_id=category.id,
                created_from_import_batch_id=import_batch.id,
                notes=f"Created from historical Excel import action: {row.action}.",
            )

    def _add_category_mapping(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
        source_file_name: str | None,
        source_category_name: str,
        source_category_key: str,
        source_column_kind: str,
        target_category_id: int,
        created_from_import_batch_id: int,
        notes: str | None = None,
    ) -> CategoryMapping:
        if hasattr(self.repository, "add_category_mapping"):
            return self.repository.add_category_mapping(
                user_profile_id=user_profile_id,
                source_system=source_system,
                source_file_hash=source_file_hash,
                source_file_name=source_file_name,
                source_category_name=source_category_name,
                source_category_key=source_category_key,
                source_column_kind=source_column_kind,
                target_category_id=target_category_id,
                created_from_import_batch_id=created_from_import_batch_id,
                notes=notes,
            )
        category_mapping = CategoryMapping(
            user_profile_id=user_profile_id,
            source_system=source_system,
            source_file_hash=source_file_hash,
            source_file_name=source_file_name,
            source_category_name=source_category_name,
            source_category_key=source_category_key,
            source_column_kind=source_column_kind,
            target_category_id=target_category_id,
            created_from_import_batch_id=created_from_import_batch_id,
            mapping_status=CategoryMappingStatus.CONFIRMED,
            notes=notes,
        )
        self.repository.session.add(category_mapping)
        return category_mapping

    def rollback_import_batch(
        self,
        *,
        user_profile_id: int,
        import_batch_id: int,
        decided_by: str,
    ) -> ImportBatch:
        if not decided_by:
            raise ValueError("Historical Excel rollback requires decided_by.")

        self.repository.session.flush()
        import_batch = self._get_import_batch(
            import_batch_id=import_batch_id,
            user_profile_id=user_profile_id,
        )
        if import_batch is None:
            raise ValueError("Import batch was not found for the user profile.")
        if import_batch.source_system != ImportSourceSystem.EXCEL_HISTORICAL:
            raise ValueError("Only historical Excel import batches can be rolled back here.")
        if import_batch.import_status == ImportStatus.ROLLED_BACK:
            raise ValueError("Historical Excel import batch is already rolled back.")

        imported_sources = self._list_imported_transaction_sources(import_batch_id)
        for imported_source in imported_sources:
            transaction = imported_source.created_transaction
            if transaction is not None:
                transaction.is_deleted = True

        import_batch.import_status = ImportStatus.ROLLED_BACK
        import_batch.notes = append_note(
            import_batch.notes,
            f"Rolled back by {decided_by}.",
        )
        return import_batch

    def _get_completed_import_batch_by_file_hash(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
    ) -> ImportBatch | None:
        statement = select(ImportBatch).where(
            ImportBatch.user_profile_id == user_profile_id,
            ImportBatch.source_system == source_system,
            ImportBatch.source_file_hash == source_file_hash,
            ImportBatch.import_status.in_(
                [ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_WARNINGS]
            ),
        )
        return self.repository.session.scalar(statement.order_by(ImportBatch.imported_at.desc()))

    def _get_category_mapping_for_source(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
        source_category_key: str,
    ) -> CategoryMapping | None:
        if hasattr(self.repository, "get_category_mapping_for_source"):
            return self.repository.get_category_mapping_for_source(
                user_profile_id=user_profile_id,
                source_system=source_system,
                source_file_hash=source_file_hash,
                source_category_key=source_category_key,
            )
        statement = select(CategoryMapping).where(
            CategoryMapping.user_profile_id == user_profile_id,
            CategoryMapping.source_system == source_system,
            CategoryMapping.source_file_hash == source_file_hash,
            CategoryMapping.source_category_key == source_category_key,
        )
        return self.repository.session.scalar(statement)

    def _get_imported_source_by_normalized_hash(
        self,
        *,
        user_profile_id: int,
        source_system: ImportSourceSystem,
        normalized_hash: str,
    ) -> ImportedTransactionSource | None:
        statement = (
            select(ImportedTransactionSource)
            .join(ImportBatch)
            .where(
                ImportBatch.user_profile_id == user_profile_id,
                ImportBatch.source_system == source_system,
                ImportBatch.import_status.in_(
                    [ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_WARNINGS]
                ),
                ImportedTransactionSource.normalized_hash == normalized_hash,
            )
        )
        return self.repository.session.scalar(
            statement.order_by(ImportedTransactionSource.id)
        )

    def _get_import_batch(
        self, *, import_batch_id: int, user_profile_id: int
    ) -> ImportBatch | None:
        statement = select(ImportBatch).where(
            ImportBatch.id == import_batch_id,
            ImportBatch.user_profile_id == user_profile_id,
        )
        return self.repository.session.scalar(statement)

    def _list_imported_transaction_sources(
        self, import_batch_id: int
    ) -> list[ImportedTransactionSource]:
        statement = select(ImportedTransactionSource).where(
            ImportedTransactionSource.import_batch_id == import_batch_id
        )
        return list(self.repository.session.scalars(statement.order_by(ImportedTransactionSource.id)))

    def _get_or_create_historical_account(
        self, *, user_profile_id: int, currency: str
    ) -> Account:
        for account in self.repository.list_accounts(
            user_profile_id,
            include_inactive=True,
        ):
            if account.name == HISTORICAL_EXCEL_ACCOUNT_NAME:
                return account
        account = self.repository.add_account(
            user_profile_id=user_profile_id,
            name=HISTORICAL_EXCEL_ACCOUNT_NAME,
            account_type=AccountType.OTHER,
            currency=currency,
            ownership_type=OwnershipType.PERSONAL,
        )
        self.repository.session.flush()
        return account

    def _resolve_categories(
        self,
        *,
        user_profile_id: int,
        category_plan: list["HistoricalCategoryPlanRow"],
    ) -> dict[str, Category]:
        categories_by_source_name: dict[str, Category] = {}
        for row in category_plan:
            if row.action in {"reuse", "map"}:
                categories_by_source_name[row.source_category_name] = row.category
                continue
            if row.action != "create":
                continue
            category = self.repository.add_category(
                user_profile_id=user_profile_id,
                name=row.target_category_name,
                category_type=row.category_type,
                canonical_key=row.canonical_key,
            )
            categories_by_source_name[row.source_category_name] = category
        return categories_by_source_name


@dataclass(frozen=True)
class HistoricalCategoryPlanRow:
    """Internal category plan for confirmed historical imports."""

    source_category_name: str
    source_category_key: str
    action: str
    target_category_name: str
    category_type: CategoryType
    canonical_key: str
    category: Category | None = None


def historical_import_blockers(preview: HistoricalExcelPreview) -> list[str]:
    validation = getattr(preview, "tracking_validation", None)
    if validation is None:
        return ["Historical Excel import requires comparable Seguimiento validation."]

    blockers = []
    if validation.difference_count:
        blockers.append("Historical Excel import has Registro/Seguimiento differences.")
    if validation.registro_only_categories:
        blockers.append("Historical Excel import has categories only in Registro.")
    if validation.seguimiento_only_categories:
        blockers.append("Historical Excel import has categories only in Seguimiento.")
    if preview.transaction_count == 0:
        blockers.append("Historical Excel import has no transaction candidates.")
    if mixed_source_category_kinds(preview):
        blockers.append("Historical Excel import has mixed income/expense source categories.")
    return blockers


def build_category_plan(
    categories: list[Category],
    preview: HistoricalExcelPreview,
    *,
    category_id_overrides_by_source_name: dict[str, int] | None = None,
) -> list[HistoricalCategoryPlanRow]:
    categories_by_normalized_name = {
        normalize_category_label(category.name): category
        for category in categories
    }
    categories_by_canonical_key = {
        category.canonical_key: category
        for category in categories
    }
    categories_by_id = {category.id: category for category in categories}
    category_overrides = category_id_overrides_by_source_name or {}
    source_category_kinds = source_category_kinds_by_name(preview)

    rows = []
    for source_category_name in preview.source_categories:
        normalized_name = normalize_category_label(source_category_name)
        canonical_key = canonical_key_from_name(source_category_name)
        category_type = category_type_from_source_kind(
            source_category_kinds.get(source_category_name, "expense")
        )
        mapped_category_id = category_overrides.get(source_category_name)
        if mapped_category_id is not None:
            mapped_category = categories_by_id.get(mapped_category_id)
            if mapped_category is None:
                raise ValueError("Mapped category was not found for the user profile.")
            if not mapped_category.is_active:
                raise ValueError("Historical Excel import cannot map to inactive categories.")
            if mapped_category.category_type != category_type:
                raise ValueError(
                    "Historical Excel import category mapping has incompatible category types."
                )
            rows.append(
                HistoricalCategoryPlanRow(
                    source_category_name=source_category_name,
                    source_category_key=normalized_name,
                    action="map",
                    target_category_name=mapped_category.name,
                    category_type=mapped_category.category_type,
                    canonical_key=mapped_category.canonical_key,
                    category=mapped_category,
                )
            )
            continue

        matched_category = categories_by_normalized_name.get(normalized_name)
        if matched_category is not None and matched_category.is_active:
            rows.append(
                HistoricalCategoryPlanRow(
                    source_category_name=source_category_name,
                    source_category_key=normalized_name,
                    action="reuse",
                    target_category_name=matched_category.name,
                    category_type=matched_category.category_type,
                    canonical_key=matched_category.canonical_key,
                    category=matched_category,
                )
            )
            continue
        if matched_category is not None and not matched_category.is_active:
            rows.append(
                HistoricalCategoryPlanRow(
                    source_category_name=source_category_name,
                    source_category_key=normalized_name,
                    action="conflict",
                    target_category_name=matched_category.name,
                    category_type=matched_category.category_type,
                    canonical_key=matched_category.canonical_key,
                    category=matched_category,
                )
            )
            continue

        conflicting_category = categories_by_canonical_key.get(canonical_key)
        if conflicting_category is not None:
            rows.append(
                HistoricalCategoryPlanRow(
                    source_category_name=source_category_name,
                    source_category_key=normalized_name,
                    action="conflict",
                    target_category_name=conflicting_category.name,
                    category_type=conflicting_category.category_type,
                    canonical_key=canonical_key,
                    category=conflicting_category,
                )
            )
            continue

        rows.append(
            HistoricalCategoryPlanRow(
                source_category_name=source_category_name,
                source_category_key=normalized_name,
                action="create",
                target_category_name=source_category_name,
                category_type=category_type,
                canonical_key=canonical_key,
            )
        )
    return rows


def source_category_kinds_by_name(preview: HistoricalExcelPreview) -> dict[str, str]:
    kinds_by_name: dict[str, str] = {}
    for candidate in preview.candidates:
        category_name = candidate.source_category_name
        category_kind = candidate_source_column_kind(candidate)
        if category_kind == "income":
            kinds_by_name[category_name] = "income"
        else:
            kinds_by_name.setdefault(category_name, "expense")
    return kinds_by_name


def mixed_source_category_kinds(preview: HistoricalExcelPreview) -> set[str]:
    kinds_by_name: dict[str, set[str]] = {}
    for candidate in preview.candidates:
        kinds_by_name.setdefault(candidate.source_category_name, set()).add(
            candidate_source_column_kind(candidate)
        )
    return {
        category_name
        for category_name, kinds in kinds_by_name.items()
        if len(kinds) > 1
    }


def category_type_from_source_kind(source_kind: str) -> CategoryType:
    if source_kind == "income":
        return CategoryType.INCOME
    return CategoryType.EXPENSE


def transaction_type_for_candidate(
    candidate: HistoricalExcelTransactionCandidate,
) -> TransactionType:
    source_kind = candidate_source_column_kind(candidate)
    source_amount = candidate_source_amount_decimal(candidate)
    if source_kind == "income":
        if source_amount < 0:
            return TransactionType.ADJUSTMENT
        return TransactionType.INCOME
    if source_amount < 0:
        return TransactionType.REFUND
    return TransactionType.EXPENSE


def description_clean_for_candidate(
    candidate: HistoricalExcelTransactionCandidate,
) -> str:
    if candidate.description_raw and candidate.description_raw.strip():
        return candidate.description_raw.strip()
    return f"Excel histórico: {candidate.source_category_name}"


def candidate_normalized_hashes(
    *,
    user_profile_id: int,
    preview: HistoricalExcelPreview,
) -> dict[tuple[int, str], str]:
    return {
        candidate_key(candidate): normalized_hash_for_candidate(
            user_profile_id=user_profile_id,
            candidate=candidate,
        )
        for candidate in preview.candidates
    }


def normalized_hash_for_candidate(
    *,
    user_profile_id: int,
    candidate: HistoricalExcelTransactionCandidate,
) -> str:
    parts = [
        str(user_profile_id),
        candidate.transaction_date.isoformat(),
        normalize_category_label(candidate.source_category_name),
        str(candidate.amount_minor),
        candidate.direction.value,
        normalize_category_label(candidate.description_raw or ""),
        candidate_source_column_kind(candidate),
        str(candidate_source_signed_amount_minor(candidate)),
    ]
    digest = hashlib.sha256()
    digest.update("|".join(parts).encode("utf-8"))
    return digest.hexdigest()


def duplicated_values(values_by_key: dict[tuple[int, str], str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values_by_key.values():
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def candidate_key(candidate: HistoricalExcelTransactionCandidate) -> tuple[int, str]:
    return candidate.row_number_source, candidate.source_category_name


def record_id_source(
    preview: HistoricalExcelPreview,
    candidate: HistoricalExcelTransactionCandidate,
) -> str:
    return (
        f"{preview.sheet_name}:"
        f"{candidate.row_number_source}:"
        f"{candidate.column_name_source}"
    )


def payload_raw_json(
    preview: HistoricalExcelPreview,
    candidate: HistoricalExcelTransactionCandidate,
) -> str:
    payload = {
        "sheet_name": preview.sheet_name,
        "source_category_name": candidate.source_category_name,
        "source_column_kind": candidate_source_column_kind(candidate),
        "row": candidate.payload_raw,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def append_note(existing_notes: str | None, note: str) -> str:
    if not existing_notes:
        return note
    return f"{existing_notes}\n{note}"


def candidate_source_column_kind(candidate) -> str:
    return getattr(candidate, "source_column_kind", "expense")


def candidate_source_amount_decimal(candidate) -> Decimal:
    source_amount_decimal = getattr(candidate, "source_amount_decimal", None)
    if source_amount_decimal is not None:
        return source_amount_decimal
    source_amount_minor = getattr(candidate, "source_amount_minor", None)
    if source_amount_minor is not None:
        return Decimal(source_amount_minor) / Decimal("100")
    amount = Decimal(candidate.amount_minor) / Decimal("100")
    if candidate_source_column_kind(candidate) == "income":
        return amount if candidate.direction == Direction.INFLOW else -amount
    return amount if candidate.direction == Direction.OUTFLOW else -amount


def candidate_source_signed_amount_minor(candidate) -> int:
    source_amount_minor = getattr(candidate, "source_amount_minor", None)
    if source_amount_minor is not None:
        return int(source_amount_minor)
    return int(candidate_source_amount_decimal(candidate) * Decimal("100"))


def canonical_key_from_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    canonical_key = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name).strip("_").lower()
    if not canonical_key:
        raise ValueError("Category name must produce a canonical key.")
    return canonical_key


def normalize_category_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    without_accents = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


__all__ = [
    "HISTORICAL_EXCEL_ACCOUNT_NAME",
    "HistoricalExcelImportResult",
    "HistoricalExcelImportService",
    "build_category_plan",
    "historical_import_blockers",
    "transaction_type_for_candidate",
]
