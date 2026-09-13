from __future__ import annotations

from datetime import date
from pathlib import Path

from lxcell.enums.core_enums import Direction, ImportSourceSystem
from lxcell.importers.pdf_statement import (
    PdfStatementDryRunImporter,
    statement_row_normalized_hash,
)


def test_pdf_statement_importer_previews_anonymized_statement(tmp_path: Path) -> None:
    statement_path = tmp_path / "statement.pdf"
    write_simple_pdf(
        statement_path,
        pages=[
            [
                ("Fecha", 43, 700),
                ("Fecha valor", 104, 700),
                ("Descripcion", 166, 700),
                ("Dinero saliente", 335, 700),
                ("Dinero entrante", 417, 700),
                ("Saldo", 535, 700),
                ("11 sept 2026", 43, 670),
                ("12 sept 2026", 104, 670),
                ("Merchant A card purchase", 166, 670),
                ("Sample continuation", 166, 658),
                ("45,67", 335, 670),
                ("954,33", 535, 670),
                ("12 sept 2026", 43, 630),
                ("12 sept 2026", 104, 630),
                ("Merchant B refund", 166, 630),
                ("10,00", 417, 630),
                ("964,33", 535, 630),
            ],
            [
                ("Fecha", 43, 700),
                ("Fecha valor", 104, 700),
                ("Descripcion", 166, 700),
                ("Dinero saliente", 335, 700),
                ("Dinero entrante", 417, 700),
                ("Saldo", 535, 700),
                ("13 sept 2026", 43, 670),
                ("13 sept 2026", 104, 670),
                ("Merchant C debit", 166, 670),
                ("1.234,56", 335, 670),
                ("-270,23", 535, 670),
            ],
        ],
    )

    preview = PdfStatementDryRunImporter().preview(statement_path)

    assert preview.source_file_name == "statement.pdf"
    assert len(preview.source_file_hash) == 64
    assert preview.page_count == 2
    assert preview.issues == ()
    assert preview.transaction_count == 3

    first, second, third = preview.candidates
    assert first.row_number_source == 1
    assert first.page_number == 1
    assert first.transaction_date == date(2026, 9, 11)
    assert first.posted_date == date(2026, 9, 12)
    assert first.description_raw == "Merchant A card purchase Sample continuation"
    assert first.description_clean == "Merchant A card purchase Sample continuation"
    assert first.amount_minor == 4567
    assert first.direction == Direction.OUTFLOW
    assert first.amount_raw == "45,67"
    assert first.currency == "EUR"
    assert first.balance_raw == "954,33"
    assert first.balance_minor == 95433
    assert len(first.content_hash) == 64

    assert second.transaction_date == date(2026, 9, 12)
    assert second.amount_minor == 1000
    assert second.direction == Direction.INFLOW
    assert second.amount_raw == "10,00"

    assert third.amount_minor == 123456
    assert third.direction == Direction.OUTFLOW
    assert third.balance_raw == "-270,23"
    assert third.balance_minor == -27023


def test_pdf_statement_importer_reports_ambiguous_amount_columns(
    tmp_path: Path,
) -> None:
    statement_path = tmp_path / "ambiguous.pdf"
    write_simple_pdf(
        statement_path,
        pages=[
            [
                ("Fecha", 43, 700),
                ("Fecha valor", 104, 700),
                ("Descripcion", 166, 700),
                ("Dinero saliente", 335, 700),
                ("Dinero entrante", 417, 700),
                ("Saldo", 535, 700),
                ("11 sept 2026", 43, 670),
                ("11 sept 2026", 104, 670),
                ("Ambiguous movement", 166, 670),
                ("12,00", 335, 670),
                ("9,00", 417, 670),
                ("997,00", 535, 670),
            ]
        ],
    )

    preview = PdfStatementDryRunImporter().preview(statement_path)

    assert preview.candidates == ()
    assert len(preview.issues) == 1
    assert preview.issues[0].page_number == 1
    assert preview.issues[0].row_number_source == 1
    assert preview.issues[0].message == "Row has both outgoing and incoming amounts."


def test_statement_row_normalized_hash_is_account_scoped(tmp_path: Path) -> None:
    statement_path = tmp_path / "statement.pdf"
    write_simple_pdf(
        statement_path,
        pages=[
            [
                ("Fecha", 43, 700),
                ("Fecha valor", 104, 700),
                ("Descripcion", 166, 700),
                ("Dinero saliente", 335, 700),
                ("Dinero entrante", 417, 700),
                ("Saldo", 535, 700),
                ("11 sept 2026", 43, 670),
                ("11 sept 2026", 104, 670),
                ("Merchant A card purchase", 166, 670),
                ("45,67", 335, 670),
                ("954,33", 535, 670),
            ]
        ],
    )
    candidate = PdfStatementDryRunImporter().preview(statement_path).candidates[0]

    first_hash = statement_row_normalized_hash(
        user_profile_id=1,
        account_id=10,
        source_system=ImportSourceSystem.BANK_PDF,
        candidate=candidate,
    )
    repeated_hash = statement_row_normalized_hash(
        user_profile_id=1,
        account_id=10,
        source_system=ImportSourceSystem.BANK_PDF,
        candidate=candidate,
    )
    other_account_hash = statement_row_normalized_hash(
        user_profile_id=1,
        account_id=11,
        source_system=ImportSourceSystem.BANK_PDF,
        candidate=candidate,
    )

    assert first_hash == repeated_hash
    assert first_hash != other_account_hash


def test_pdf_statement_importer_ignores_pages_without_statement_header(
    tmp_path: Path,
) -> None:
    statement_path = tmp_path / "no-header.pdf"
    write_simple_pdf(statement_path, pages=[[("Resumen de saldo", 43, 700)]])

    preview = PdfStatementDryRunImporter().preview(statement_path)

    assert preview.transaction_count == 0
    assert preview.issues == ()


def write_simple_pdf(
    path: Path,
    *,
    pages: list[list[tuple[str, int, int]]],
) -> None:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [] /Count 0 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_object_ids: list[int] = []
    for page_text in pages:
        content = "\n".join(
            f"BT /F1 8 Tf 1 0 0 1 {x} {y} Tm ({escape_pdf_text(text)}) Tj ET"
            for text, x, y in page_text
        ).encode("ascii")
        content_object_id = len(objects) + 1
        objects.append(
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        page_object_id = len(objects) + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {content_object_id} 0 R >>"
            ).encode("ascii")
        )
        page_object_ids.append(page_object_id)

    page_references = " ".join(f"{object_id} 0 R" for object_id in page_object_ids)
    objects[1] = (
        f"<< /Type /Pages /Kids [{page_references}] /Count {len(page_object_ids)} >>"
    ).encode("ascii")

    offsets: list[int] = []
    pdf = bytearray(b"%PDF-1.4\n")
    for object_id, content in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(content)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(pdf))


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
