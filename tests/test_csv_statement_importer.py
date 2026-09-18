from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from lxcell.enums.core_enums import Direction
from lxcell.importers.csv_statement import (
    ING_CSV_HEADERS,
    StatementCsvDryRunImporter,
    parse_ing_csv_amount_minor,
)


def test_csv_statement_importer_previews_ing_statement(tmp_path: Path):
    statement_path = tmp_path / "ing_statement.csv"
    write_ing_csv(
        statement_path,
        rows=[
            [
                "20260601",
                "Merchant A",
                "Shared account ending 1234",
                "Counterparty A",
                "GT",
                "Debit",
                "12,34",
                "SEPA direct debit",
                "",
            ],
            [
                "20260602",
                "Sample transfer",
                "Shared account ending 1234",
                "Counterparty B",
                "BA",
                "Credit",
                "1000,00",
                "Online Banking",
                "",
            ],
        ],
    )

    preview = StatementCsvDryRunImporter().preview(statement_path)

    assert preview.source_file_name == "ing_statement.csv"
    assert len(preview.source_file_hash) == 64
    assert preview.page_count == 1
    assert preview.issues == ()
    assert preview.transaction_count == 2
    assert preview.account_hint_text == "Shared account ending 1234"

    first, second = preview.candidates
    assert first.row_number_source == 1
    assert first.transaction_date == date(2026, 6, 1)
    assert first.posted_date is None
    assert first.description_raw == "Merchant A Counterparty A"
    assert first.amount_minor == 1234
    assert first.direction == Direction.OUTFLOW
    assert first.balance_minor is None
    assert first.payload_raw["layout"] == "ing_csv_v1"
    assert first.payload_raw["account_hint_present"] is True
    assert "Account" not in first.payload_raw

    assert second.transaction_date == date(2026, 6, 2)
    assert second.amount_minor == 100000
    assert second.direction == Direction.INFLOW


def test_csv_statement_importer_rejects_unknown_headers(tmp_path: Path):
    statement_path = tmp_path / "unsupported.csv"
    statement_path.write_text("Date,Amount\n20260601,12,34\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported statement CSV layout"):
        StatementCsvDryRunImporter().preview(statement_path)


def test_csv_statement_importer_reports_row_parse_issues(tmp_path: Path):
    statement_path = tmp_path / "ing_statement.csv"
    write_ing_csv(
        statement_path,
        rows=[
            [
                "20260601",
                "Merchant A",
                "Shared account ending 1234",
                "Counterparty A",
                "GT",
                "Debit",
                "12,34",
                "SEPA direct debit",
                "",
            ],
            [
                "bad-date",
                "Merchant B",
                "Shared account ending 1234",
                "Counterparty B",
                "GT",
                "Debit",
                "56,78",
                "Transfer",
                "",
            ],
        ],
    )

    preview = StatementCsvDryRunImporter().preview(statement_path)

    assert preview.transaction_count == 1
    assert len(preview.issues) == 1
    assert preview.issues[0].row_number_source == 2
    assert preview.issues[0].message == "Row has an invalid ING CSV date."


def test_parse_ing_csv_amount_minor_handles_comma_decimals():
    assert parse_ing_csv_amount_minor("1.234,56") == 123456
    assert parse_ing_csv_amount_minor("-12,34") == 1234


def write_ing_csv(path: Path, *, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(ING_CSV_HEADERS)
        writer.writerows(rows)
