"""Confirmed PDF statement import workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from lxcell.db.models import Account, ImportedTransactionSource
from lxcell.enums.core_enums import (
    ClassificationDecisionSource,
    ClassificationDecisionStatus,
    Direction,
    ImportAction,
    ImportSourceSystem,
    ImportStatus,
    PaymentMethod,
    TransactionReviewStatus,
    TransactionSourceType,
    TransactionType,
)
from lxcell.importers import (
    PdfStatementPreview,
    PdfStatementTransactionCandidate,
    statement_row_normalized_hash,
)
from lxcell.repositories import AccountingRepository
from lxcell.services.accounting_service import protected_transaction_dates
from lxcell.services.deterministic_classification_service import (
    DeterministicClassificationService,
)


@dataclass(frozen=True)
class StatementPdfImportResult:
    """Summary of a confirmed PDF statement import."""

    import_batch_id: int
    account_id: int
    transaction_count: int
    ignored_protected_count: int
    matched_existing_count: int
    marked_duplicate_count: int

    @property
    def source_row_count(self) -> int:
        return (
            self.transaction_count
            + self.ignored_protected_count
            + self.matched_existing_count
            + self.marked_duplicate_count
        )


class StatementPdfImportService:
    """Service for writing reviewed statement PDF previews to the database."""

    SUPPORTED_SOURCE_SYSTEMS = {
        ImportSourceSystem.BANK_PDF,
        ImportSourceSystem.CARD_PDF,
    }

    def __init__(self, repository: AccountingRepository) -> None:
        self.repository = repository

    def confirm_import(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        source_system: ImportSourceSystem,
        preview: PdfStatementPreview,
        confirmed_by: str,
        user_confirmed: bool,
        allow_locked_period_override: bool = False,
    ) -> StatementPdfImportResult:
        if not user_confirmed:
            raise ValueError("Statement PDF import requires explicit confirmation.")
        if not confirmed_by:
            raise ValueError("Statement PDF import requires confirmed_by.")
        if source_system not in self.SUPPORTED_SOURCE_SYSTEMS:
            raise ValueError("Statement PDF import requires a PDF source system.")
        if preview.issues:
            raise ValueError("Statement PDF import has parse issues.")
        if preview.transaction_count == 0:
            raise ValueError("Statement PDF import has no transaction candidates.")

        user_profile = self.repository.get_user_profile(user_profile_id)
        if user_profile is None:
            raise ValueError("User profile was not found.")
        account = self._get_active_account(
            user_profile_id=user_profile_id,
            account_id=account_id,
        )

        duplicate_batch = self._get_completed_import_batch_by_file_hash_for_account(
            user_profile_id=user_profile_id,
            account_id=account_id,
            source_system=source_system,
            source_file_hash=preview.source_file_hash,
        )
        if duplicate_batch is not None:
            raise ValueError("This statement PDF file was already imported.")

        import_batch = self.repository.add_import_batch(
            user_profile_id=user_profile_id,
            account_id=account_id,
            source_system=source_system,
            source_file_name=preview.source_file_name,
            source_file_hash=preview.source_file_hash,
            import_status=ImportStatus.PENDING,
            imported_by=confirmed_by,
            notes="Confirmed statement PDF import.",
        )
        self.repository.session.flush()

        seen_normalized_hashes: set[str] = set()
        transaction_count = 0
        ignored_protected_count = 0
        matched_existing_count = 0
        marked_duplicate_count = 0

        for candidate in preview.candidates:
            normalized_hash = statement_row_normalized_hash(
                user_profile_id=user_profile_id,
                account_id=account_id,
                source_system=source_system,
                candidate=candidate,
            )
            if normalized_hash in seen_normalized_hashes:
                self._record_source(
                    import_batch_id=import_batch.id,
                    candidate=candidate,
                    import_action=ImportAction.MARKED_DUPLICATE,
                    normalized_hash=normalized_hash,
                    payload_extra={"skip_reason": "duplicate_row_in_same_preview"},
                )
                marked_duplicate_count += 1
                continue
            seen_normalized_hashes.add(normalized_hash)

            existing_source = self._get_imported_source_by_normalized_hash_for_account(
                user_profile_id=user_profile_id,
                account_id=account_id,
                source_system=source_system,
                normalized_hash=normalized_hash,
            )
            if existing_source is not None:
                self._record_source(
                    import_batch_id=import_batch.id,
                    candidate=candidate,
                    import_action=ImportAction.MATCHED_EXISTING,
                    normalized_hash=normalized_hash,
                    payload_extra=matched_existing_payload(existing_source),
                )
                matched_existing_count += 1
                continue

            if (
                not allow_locked_period_override
                and protected_transaction_dates(user_profile, [candidate.transaction_date])
            ):
                self._record_source(
                    import_batch_id=import_batch.id,
                    candidate=candidate,
                    import_action=ImportAction.IGNORED,
                    normalized_hash=normalized_hash,
                    payload_extra={"skip_reason": "protected_period"},
                )
                ignored_protected_count += 1
                continue

            transaction = self.repository.add_transaction(
                user_profile_id=user_profile_id,
                account_id=account_id,
                transaction_date=candidate.transaction_date,
                posted_date=candidate.posted_date,
                description_clean=candidate.description_clean,
                description_raw=candidate.description_raw,
                amount_minor=candidate.amount_minor,
                currency=candidate.currency or account.currency,
                direction=candidate.direction,
                transaction_type=transaction_type_for_statement_candidate(candidate),
                payment_method=payment_method_for_statement_candidate(
                    source_system,
                    candidate,
                ),
                review_status=TransactionReviewStatus.PENDING_REVIEW,
                source_type=TransactionSourceType.BANK_IMPORT,
                source_id=normalized_hash,
            )
            self.repository.session.flush()
            self._record_source(
                import_batch_id=import_batch.id,
                candidate=candidate,
                import_action=ImportAction.CREATED_TRANSACTION,
                normalized_hash=normalized_hash,
                created_transaction_id=transaction.id,
            )
            classification_result = DeterministicClassificationService(
                self.repository
            ).classify_and_record_transaction(
                user_profile_id=user_profile_id,
                transaction=transaction,
                decided_by="system",
            )
            if classification_result is None:
                self.repository.add_classification_decision(
                    transaction_id=transaction.id,
                    transaction_type=transaction.transaction_type,
                    payment_method=transaction.payment_method,
                    decision_source=ClassificationDecisionSource.IMPORT_DEFAULT,
                    decision_status=ClassificationDecisionStatus.SUGGESTED,
                    decided_by="system",
                    notes="Statement PDF import default.",
                )
            transaction_count += 1

        if ignored_protected_count or matched_existing_count or marked_duplicate_count:
            import_batch.import_status = ImportStatus.COMPLETED_WITH_WARNINGS
        else:
            import_batch.import_status = ImportStatus.COMPLETED
        self.repository.session.flush()

        return StatementPdfImportResult(
            import_batch_id=import_batch.id,
            account_id=account_id,
            transaction_count=transaction_count,
            ignored_protected_count=ignored_protected_count,
            matched_existing_count=matched_existing_count,
            marked_duplicate_count=marked_duplicate_count,
        )

    def _record_source(
        self,
        *,
        import_batch_id: int,
        candidate: PdfStatementTransactionCandidate,
        import_action: ImportAction,
        normalized_hash: str,
        created_transaction_id: int | None = None,
        payload_extra: dict | None = None,
    ) -> None:
        self.repository.add_imported_transaction_source(
            import_batch_id=import_batch_id,
            import_action=import_action,
            row_number_source=candidate.row_number_source,
            record_id_source=record_id_source(candidate),
            date_raw=candidate.payload_raw.get("transaction_date_raw")
            or candidate.transaction_date.isoformat(),
            description_raw=candidate.description_raw,
            amount_raw=candidate.amount_raw,
            currency_raw=candidate.currency,
            payload_raw_json=payload_raw_json(candidate, extra=payload_extra),
            normalized_hash=normalized_hash,
            created_transaction_id=created_transaction_id,
        )

    def _get_active_account(self, *, user_profile_id: int, account_id: int) -> Account:
        if hasattr(self.repository, "get_account"):
            account = self.repository.get_account(
                account_id=account_id,
                user_profile_id=user_profile_id,
            )
        else:
            account = next(
                (
                    account
                    for account in self.repository.list_accounts(
                        user_profile_id,
                        include_inactive=True,
                    )
                    if account.id == account_id
                ),
                None,
            )
        if account is None:
            raise ValueError("Account was not found for the user profile.")
        if not account.is_active:
            raise ValueError("Statement PDF import requires an active account.")
        return account

    def _get_completed_import_batch_by_file_hash_for_account(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        source_system: ImportSourceSystem,
        source_file_hash: str,
    ):
        if hasattr(self.repository, "get_completed_import_batch_by_file_hash_for_account"):
            return self.repository.get_completed_import_batch_by_file_hash_for_account(
                user_profile_id=user_profile_id,
                account_id=account_id,
                source_system=source_system,
                source_file_hash=source_file_hash,
            )
        return None

    def _get_imported_source_by_normalized_hash_for_account(
        self,
        *,
        user_profile_id: int,
        account_id: int,
        source_system: ImportSourceSystem,
        normalized_hash: str,
    ):
        if hasattr(self.repository, "get_imported_source_by_normalized_hash_for_account"):
            return self.repository.get_imported_source_by_normalized_hash_for_account(
                user_profile_id=user_profile_id,
                account_id=account_id,
                source_system=source_system,
                normalized_hash=normalized_hash,
            )
        return self.repository.get_imported_source_by_normalized_hash(
            user_profile_id=user_profile_id,
            source_system=source_system,
            normalized_hash=normalized_hash,
        )


def transaction_type_for_statement_candidate(
    candidate: PdfStatementTransactionCandidate,
) -> TransactionType:
    if candidate.direction == Direction.OUTFLOW:
        return TransactionType.EXPENSE
    return TransactionType.ADJUSTMENT


def payment_method_for_statement_source(
    source_system: ImportSourceSystem,
) -> PaymentMethod | None:
    if source_system == ImportSourceSystem.CARD_PDF:
        return PaymentMethod.CARD
    return None


def payment_method_for_statement_candidate(
    source_system: ImportSourceSystem,
    candidate: PdfStatementTransactionCandidate,
) -> PaymentMethod | None:
    description = f"{candidate.description_clean or ''} {candidate.description_raw or ''}"
    if description_indicates_peer_to_peer_payment(description):
        return PaymentMethod.PEER_TO_PEER
    if description_indicates_card_payment(description):
        return PaymentMethod.CARD
    return payment_method_for_statement_source(source_system)


def description_indicates_peer_to_peer_payment(description: str) -> bool:
    return re.search(r"\b(?:tikkie|bizum)\b", description, flags=re.IGNORECASE) is not None


def description_indicates_card_payment(description: str) -> bool:
    return re.search(r"\b(?:tarjeta|card)\b", description, flags=re.IGNORECASE) is not None


def record_id_source(candidate: PdfStatementTransactionCandidate) -> str:
    return f"page:{candidate.page_number}:row:{candidate.row_number_source}"


def matched_existing_payload(existing_source: ImportedTransactionSource) -> dict:
    payload = {
        "skip_reason": "matched_existing_source_row",
        "matched_source_id": existing_source.id,
    }
    if existing_source.created_transaction_id is not None:
        payload["matched_transaction_id"] = existing_source.created_transaction_id
    return payload


def payload_raw_json(
    candidate: PdfStatementTransactionCandidate,
    *,
    extra: dict | None = None,
) -> str:
    payload = {
        "content_hash": candidate.content_hash,
        "page_number": candidate.page_number,
        "row": candidate.payload_raw,
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = [
    "StatementPdfImportResult",
    "StatementPdfImportService",
    "description_indicates_card_payment",
    "description_indicates_peer_to_peer_payment",
    "payment_method_for_statement_candidate",
    "payment_method_for_statement_source",
    "transaction_type_for_statement_candidate",
]
