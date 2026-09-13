"""Read-only parser for Spanish PDF bank and card statements."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pdfplumber

from lxcell.enums.core_enums import Direction

DATE_COLUMN_MAX_X = 96
VALUE_DATE_COLUMN_MIN_X = 96
VALUE_DATE_COLUMN_MAX_X = 156
DESCRIPTION_COLUMN_MIN_X = 156
DESCRIPTION_COLUMN_MAX_X = 330
OUTGOING_COLUMN_MIN_X = 320
OUTGOING_COLUMN_MAX_X = 405
INCOMING_COLUMN_MIN_X = 405
INCOMING_COLUMN_MAX_X = 505
BALANCE_COLUMN_MIN_X = 505
LINE_TOP_TOLERANCE = 3

MONTH_NAMES = {
    "enero": 1,
    "ene": 1,
    "febrero": 2,
    "feb": 2,
    "marzo": 3,
    "mar": 3,
    "abril": 4,
    "abr": 4,
    "mayo": 5,
    "may": 5,
    "junio": 6,
    "jun": 6,
    "julio": 7,
    "jul": 7,
    "agosto": 8,
    "ago": 8,
    "septiembre": 9,
    "sept": 9,
    "sep": 9,
    "setiembre": 9,
    "octubre": 10,
    "oct": 10,
    "noviembre": 11,
    "nov": 11,
    "diciembre": 12,
    "dic": 12,
}

DATE_NUMERIC_PATTERN = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})$")
AMOUNT_PATTERN = re.compile(
    r"^-?\d{1,3}(?:[. ]\d{3})*,\d{2}€?$|^-?\d+,\d{2}€?$"
)


@dataclass(frozen=True)
class PdfStatementTransactionCandidate:
    """One transaction candidate read from a statement PDF."""

    row_number_source: int
    page_number: int
    transaction_date: date
    posted_date: date | None
    description_raw: str
    description_clean: str
    amount_minor: int
    direction: Direction
    amount_raw: str
    currency: str
    balance_raw: str | None
    balance_minor: int | None
    payload_raw: dict[str, str | int | float | None]
    content_hash: str


@dataclass(frozen=True)
class PdfStatementParseIssue:
    """A non-fatal parsing issue found during statement preview."""

    page_number: int
    row_number_source: int | None
    message: str


@dataclass(frozen=True)
class PdfStatementPreview:
    """Read-only preview of a statement PDF import."""

    source_file_name: str
    source_file_hash: str
    page_count: int
    candidates: tuple[PdfStatementTransactionCandidate, ...]
    issues: tuple[PdfStatementParseIssue, ...]

    @property
    def transaction_count(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True)
class PositionedWord:
    """A PDF word plus the layout coordinates needed for column parsing."""

    text: str
    x0: float
    top: float


class PdfStatementDryRunImporter:
    """Parse Spanish statement PDFs without writing to the database."""

    def preview(self, statement_path: str | Path) -> PdfStatementPreview:
        path = Path(statement_path)
        candidates: list[PdfStatementTransactionCandidate] = []
        issues: list[PdfStatementParseIssue] = []

        with pdfplumber.open(path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                words = [
                    PositionedWord(
                        text=str(word["text"]),
                        x0=float(word["x0"]),
                        top=float(word["top"]),
                    )
                    for word in page.extract_words(x_tolerance=1, y_tolerance=3)
                ]
                page_rows = parse_page_words(
                    words,
                    page_number=page_index,
                    first_row_number=len(candidates) + 1,
                )
                candidates.extend(page_rows.candidates)
                issues.extend(page_rows.issues)

            page_count = len(pdf.pages)

        return PdfStatementPreview(
            source_file_name=path.name,
            source_file_hash=file_sha256(path),
            page_count=page_count,
            candidates=tuple(candidates),
            issues=tuple(issues),
        )


@dataclass(frozen=True)
class PageParseResult:
    """Parsed statement rows for one PDF page."""

    candidates: tuple[PdfStatementTransactionCandidate, ...]
    issues: tuple[PdfStatementParseIssue, ...]


def parse_page_words(
    words: list[PositionedWord],
    *,
    page_number: int,
    first_row_number: int,
) -> PageParseResult:
    header_top = find_statement_header_top(words)
    if header_top is None:
        return PageParseResult(candidates=(), issues=())

    lines = group_words_by_line(
        [
            word
            for word in words
            if word.top > header_top + 8
            and not is_page_footer_word(word)
            and word.text.strip()
        ]
    )
    row_groups: list[list[list[PositionedWord]]] = []
    current_row: list[list[PositionedWord]] = []

    for line in lines:
        if is_transaction_start_line(line):
            if current_row:
                row_groups.append(current_row)
            current_row = [line]
        elif current_row:
            current_row.append(line)

    if current_row:
        row_groups.append(current_row)

    candidates: list[PdfStatementTransactionCandidate] = []
    issues: list[PdfStatementParseIssue] = []
    next_row_number = first_row_number

    for row_group in row_groups:
        candidate, issue = parse_row_group(
            row_group,
            page_number=page_number,
            row_number_source=next_row_number,
        )
        if candidate is not None:
            candidates.append(candidate)
            next_row_number += 1
        elif issue is not None:
            issues.append(issue)

    return PageParseResult(candidates=tuple(candidates), issues=tuple(issues))


def find_statement_header_top(words: list[PositionedWord]) -> float | None:
    header_tops: list[float] = []
    for line in group_words_by_line(words):
        text_by_x = {normalize_text(word.text) for word in line}
        line_text = " ".join(word.text.lower() for word in line)
        if (
            "fecha" in text_by_x
            and "descripcion" in text_by_x
            and "dinero" in text_by_x
            and ("saliente" in text_by_x or "entrante" in text_by_x)
        ) or (
            "fecha valor" in line_text
            and "descripcion" in normalize_text(line_text)
            and "dinero saliente" in line_text
        ):
            header_tops.append(max(word.top for word in line))
    if not header_tops:
        return None
    return max(header_tops)


def group_words_by_line(words: list[PositionedWord]) -> list[list[PositionedWord]]:
    lines: list[list[PositionedWord]] = []
    for word in sorted(words, key=lambda item: (item.top, item.x0)):
        for line in lines:
            if abs(line[0].top - word.top) <= LINE_TOP_TOLERANCE:
                line.append(word)
                break
        else:
            lines.append([word])

    return [sorted(line, key=lambda item: item.x0) for line in lines]


def is_transaction_start_line(line: list[PositionedWord]) -> bool:
    return parse_date_from_column(line, 0, DATE_COLUMN_MAX_X) is not None


def parse_row_group(
    row_group: list[list[PositionedWord]],
    *,
    page_number: int,
    row_number_source: int,
) -> tuple[PdfStatementTransactionCandidate | None, PdfStatementParseIssue | None]:
    first_line = row_group[0]
    transaction_date = parse_date_from_column(first_line, 0, DATE_COLUMN_MAX_X)
    if transaction_date is None:
        return None, None
    posted_date = parse_date_from_column(
        first_line,
        VALUE_DATE_COLUMN_MIN_X,
        VALUE_DATE_COLUMN_MAX_X,
    )

    outgoing_amount = first_amount_in_column(
        row_group,
        OUTGOING_COLUMN_MIN_X,
        OUTGOING_COLUMN_MAX_X,
    )
    incoming_amount = first_amount_in_column(
        row_group,
        INCOMING_COLUMN_MIN_X,
        INCOMING_COLUMN_MAX_X,
    )
    if outgoing_amount is not None and incoming_amount is not None:
        return None, PdfStatementParseIssue(
            page_number=page_number,
            row_number_source=row_number_source,
            message="Row has both outgoing and incoming amounts.",
        )
    if outgoing_amount is None and incoming_amount is None:
        return None, PdfStatementParseIssue(
            page_number=page_number,
            row_number_source=row_number_source,
            message="Row has no outgoing or incoming amount.",
        )

    direction = Direction.OUTFLOW if outgoing_amount is not None else Direction.INFLOW
    amount_raw = outgoing_amount or incoming_amount
    if amount_raw is None:
        raise ValueError("Amount raw value should be present after validation.")
    amount_minor = decimal_to_minor_units(parse_decimal_amount(amount_raw))
    balance_raw = first_amount_in_column(
        row_group,
        BALANCE_COLUMN_MIN_X,
        float("inf"),
    )
    balance_minor = (
        signed_decimal_to_minor_units(parse_decimal_amount(balance_raw))
        if balance_raw is not None
        else None
    )
    description_raw = description_from_row_group(row_group)
    description_clean = clean_description(description_raw)
    payload_raw: dict[str, str | int | float | None] = {
        "page_number": page_number,
        "row_top": round(first_line[0].top, 2),
        "transaction_date_raw": words_text_in_column(first_line, 0, DATE_COLUMN_MAX_X),
        "posted_date_raw": words_text_in_column(
            first_line,
            VALUE_DATE_COLUMN_MIN_X,
            VALUE_DATE_COLUMN_MAX_X,
        ),
        "description_raw": description_raw,
        "money_out_raw": outgoing_amount,
        "money_in_raw": incoming_amount,
        "balance_raw": balance_raw,
    }
    content_hash = source_row_content_hash(
        transaction_date=transaction_date,
        posted_date=posted_date,
        description_clean=description_clean,
        amount_minor=amount_minor,
        direction=direction,
        currency="EUR",
        balance_minor=balance_minor,
    )

    return PdfStatementTransactionCandidate(
        row_number_source=row_number_source,
        page_number=page_number,
        transaction_date=transaction_date,
        posted_date=posted_date,
        description_raw=description_raw,
        description_clean=description_clean,
        amount_minor=amount_minor,
        direction=direction,
        amount_raw=amount_raw,
        currency="EUR",
        balance_raw=balance_raw,
        balance_minor=balance_minor,
        payload_raw=payload_raw,
        content_hash=content_hash,
    ), None


def parse_date_from_column(
    line: list[PositionedWord],
    min_x: float,
    max_x: float,
) -> date | None:
    tokens = [word.text for word in line if min_x <= word.x0 < max_x]
    return parse_statement_date(tokens)


def parse_statement_date(tokens: list[str]) -> date | None:
    cleaned_tokens = [token.strip(",.;:") for token in tokens if token.strip(",.;:")]
    for token in cleaned_tokens:
        match = DATE_NUMERIC_PATTERN.match(token)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            if year < 100:
                year += 2000
            return date(year, month, day)

    for index in range(len(cleaned_tokens) - 2):
        day_token, month_token, year_token = cleaned_tokens[index : index + 3]
        if not day_token.isdigit() or not year_token.isdigit():
            continue
        month = MONTH_NAMES.get(normalize_text(month_token))
        if month is None:
            continue
        return date(int(year_token), month, int(day_token))

    return None


def first_amount_in_column(
    row_group: list[list[PositionedWord]],
    min_x: float,
    max_x: float,
) -> str | None:
    for line in row_group:
        for word in line:
            if min_x <= word.x0 < max_x and is_eur_amount(word.text):
                return word.text
    return None


def is_eur_amount(text: str) -> bool:
    return bool(AMOUNT_PATTERN.match(text.strip()))


def parse_decimal_amount(text: str) -> Decimal:
    normalized = text.strip().removesuffix("€").replace(" ", "").replace(".", "")
    normalized = normalized.replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"Could not parse amount '{text}'.") from exc


def decimal_to_minor_units(amount: Decimal) -> int:
    cents = (abs(amount) * Decimal("100")).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(cents)


def signed_decimal_to_minor_units(amount: Decimal) -> int:
    cents = (amount * Decimal("100")).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(cents)


def description_from_row_group(row_group: list[list[PositionedWord]]) -> str:
    description_words: list[str] = []
    for line in row_group:
        for word in line:
            if (
                DESCRIPTION_COLUMN_MIN_X <= word.x0 < DESCRIPTION_COLUMN_MAX_X
                and not is_eur_amount(word.text)
            ):
                description_words.append(word.text)
    return " ".join(description_words).strip()


def clean_description(description: str) -> str:
    return " ".join(description.split())


def words_text_in_column(
    line: list[PositionedWord],
    min_x: float,
    max_x: float,
) -> str:
    return " ".join(word.text for word in line if min_x <= word.x0 < max_x).strip()


def source_row_content_hash(
    *,
    transaction_date: date,
    posted_date: date | None,
    description_clean: str,
    amount_minor: int,
    direction: Direction,
    currency: str,
    balance_minor: int | None,
) -> str:
    payload = {
        "amount_minor": amount_minor,
        "balance_minor": balance_minor,
        "currency": currency,
        "description_clean": normalize_text(description_clean),
        "direction": direction.value,
        "posted_date": posted_date.isoformat() if posted_date else None,
        "transaction_date": transaction_date.isoformat(),
    }
    return stable_hash(payload)


def statement_row_normalized_hash(
    *,
    user_profile_id: int,
    account_id: int,
    source_system: str,
    candidate: PdfStatementTransactionCandidate,
    record_id_source: str | None = None,
) -> str:
    """Build the account-scoped row hash for ImportedTransactionSource."""

    payload: dict[str, Any] = {
        "account_id": account_id,
        "amount_minor": candidate.amount_minor,
        "balance_minor": candidate.balance_minor,
        "currency": candidate.currency,
        "description_clean": normalize_text(candidate.description_clean),
        "direction": candidate.direction.value,
        "posted_date": (
            candidate.posted_date.isoformat() if candidate.posted_date else None
        ),
        "record_id_source": record_id_source,
        "source_system": source_system,
        "transaction_date": candidate.transaction_date.isoformat(),
        "user_profile_id": user_profile_id,
    }
    return stable_hash(payload)


def stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_value.casefold().split())


def is_page_footer_word(word: PositionedWord) -> bool:
    return word.top > 780


__all__ = [
    "PdfStatementDryRunImporter",
    "PdfStatementParseIssue",
    "PdfStatementPreview",
    "PdfStatementTransactionCandidate",
    "statement_row_normalized_hash",
]
