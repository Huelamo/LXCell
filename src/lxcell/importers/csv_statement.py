"""Read-only parser for bank statement CSV exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from lxcell.enums.core_enums import Direction
from lxcell.importers.pdf_statement import (
    PdfStatementParseIssue,
    PdfStatementPreview,
    PdfStatementTransactionCandidate,
    clean_description,
    file_sha256,
    source_row_content_hash,
)

ING_CSV_HEADERS = (
    "Date",
    "Name / Description",
    "Account",
    "Counterparty",
    "Code",
    "Debit/credit",
    "Amount (EUR)",
    "Transaction type",
    "Notifications",
)


@dataclass(frozen=True)
class CsvStatementLayout:
    """A known CSV statement layout."""

    name: str
    headers: tuple[str, ...]


ING_CSV_LAYOUT = CsvStatementLayout(name="ing_csv_v1", headers=ING_CSV_HEADERS)


class StatementCsvDryRunImporter:
    """Parse supported statement CSV exports without writing to the database."""

    def preview(self, statement_path: str | Path) -> PdfStatementPreview:
        path = Path(statement_path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            detect_csv_statement_layout(tuple(reader.fieldnames or ()))
            candidates: list[PdfStatementTransactionCandidate] = []
            issues: list[PdfStatementParseIssue] = []
            account_hints: set[str] = set()

            for row_number, row in enumerate(reader, start=1):
                account_hint = row.get("Account", "").strip()
                if account_hint:
                    account_hints.add(account_hint)
                candidate, issue = parse_ing_csv_row(row, row_number=row_number)
                if candidate is not None:
                    candidates.append(candidate)
                if issue is not None:
                    issues.append(issue)

        return PdfStatementPreview(
            source_file_name=path.name,
            source_file_hash=file_sha256(path),
            page_count=1,
            candidates=tuple(candidates),
            issues=tuple(issues),
            account_hint_text=" ".join(sorted(account_hints)) or None,
        )


def detect_csv_statement_layout(headers: tuple[str, ...]) -> CsvStatementLayout:
    """Return the supported layout matching the CSV headers."""
    if headers == ING_CSV_LAYOUT.headers:
        return ING_CSV_LAYOUT
    raise ValueError("Unsupported statement CSV layout.")


def parse_ing_csv_row(
    row: dict[str, str],
    *,
    row_number: int,
) -> tuple[PdfStatementTransactionCandidate | None, PdfStatementParseIssue | None]:
    try:
        transaction_date = parse_ing_csv_date(row.get("Date", ""))
        direction = parse_ing_csv_direction(row.get("Debit/credit", ""))
        amount_minor = parse_ing_csv_amount_minor(row.get("Amount (EUR)", ""))
    except ValueError as exc:
        return None, PdfStatementParseIssue(
            page_number=1,
            row_number_source=row_number,
            message=str(exc),
        )

    description_raw = ing_csv_description(row)
    description_clean = clean_description(description_raw)
    currency = "EUR"
    payload_raw: dict[str, str | int | float | None] = {
        "layout": ING_CSV_LAYOUT.name,
        "date_raw": row.get("Date", ""),
        "description_raw": row.get("Name / Description", ""),
        "counterparty_raw": row.get("Counterparty", ""),
        "code_raw": row.get("Code", ""),
        "debit_credit_raw": row.get("Debit/credit", ""),
        "amount_raw": row.get("Amount (EUR)", ""),
        "transaction_type_raw": row.get("Transaction type", ""),
        "notifications_raw": row.get("Notifications", ""),
        "account_hint_present": bool(row.get("Account", "").strip()),
    }
    content_hash = source_row_content_hash(
        transaction_date=transaction_date,
        posted_date=None,
        description_clean=description_clean,
        amount_minor=amount_minor,
        direction=direction,
        currency=currency,
        balance_minor=None,
    )

    return (
        PdfStatementTransactionCandidate(
            row_number_source=row_number,
            page_number=1,
            transaction_date=transaction_date,
            posted_date=None,
            description_raw=description_raw,
            description_clean=description_clean,
            amount_minor=amount_minor,
            direction=direction,
            amount_raw=row.get("Amount (EUR)", ""),
            currency=currency,
            balance_raw=None,
            balance_minor=None,
            payload_raw=payload_raw,
            content_hash=content_hash,
        ),
        None,
    )


def parse_ing_csv_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("Row has an invalid ING CSV date.") from exc


def parse_ing_csv_direction(value: str) -> Direction:
    normalized = value.strip().casefold()
    if normalized == "debit":
        return Direction.OUTFLOW
    if normalized == "credit":
        return Direction.INFLOW
    raise ValueError("Row has an invalid ING CSV debit/credit value.")


def parse_ing_csv_amount_minor(value: str) -> int:
    normalized = value.strip().replace(".", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Row has an invalid ING CSV amount.") from exc
    cents = (abs(amount) * Decimal("100")).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(cents)


def ing_csv_description(row: dict[str, str]) -> str:
    parts = [
        row.get("Name / Description", "").strip(),
        row.get("Counterparty", "").strip(),
    ]
    return clean_description(" ".join(part for part in parts if part))


__all__ = [
    "ING_CSV_HEADERS",
    "StatementCsvDryRunImporter",
    "detect_csv_statement_layout",
    "parse_ing_csv_amount_minor",
    "parse_ing_csv_date",
    "parse_ing_csv_direction",
]
