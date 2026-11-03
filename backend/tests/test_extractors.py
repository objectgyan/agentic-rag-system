"""Extractors against real files built in-memory — they guard upgrades of the parsing libraries.

pypdf, python-docx, openpyxl/pandas and Pillow parse untrusted uploads, so they must be kept
current; these tests are what makes bumping them safe.
"""

import io

import pytest

from app.services.processing.extractors import get_extractor


def _pdf(lines):
    """A minimal, valid one-page PDF with one text line per entry (correct xref offsets)."""
    ops = ["BT /F1 12 Tf 72 720 Td 14 TL"]
    for i, line in enumerate(lines):
        ops.append(f"({line}) Tj" if i == 0 else f"T* ({line}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def test_pdf_text_and_page_count():
    result = get_extractor("pdf").extract(_pdf(["Invoice 42", "Total due 1,250 USD"]))
    assert "Invoice 42" in result.text
    assert "Total due 1,250 USD" in result.text
    assert result.metadata == {"page_count": 1}
    assert len(result.pages) == 1


def test_pdf_rejects_garbage():
    with pytest.raises(Exception):
        get_extractor("pdf").extract(b"not a pdf at all")


def test_docx_paragraphs_skip_blank_lines():
    from docx import Document

    doc = Document()
    doc.add_paragraph("Refund policy")
    doc.add_paragraph("   ")
    doc.add_paragraph("Refunds within 30 days.")
    buf = io.BytesIO()
    doc.save(buf)
    assert get_extractor("docx").extract(buf.getvalue()).text == "Refund policy\n\nRefunds within 30 days."


def test_xlsx_every_sheet_as_key_value_rows():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Prices"
    ws.append(["sku", "price"])
    ws.append(["A-1", 9.5])
    wb.create_sheet("Notes").append(["note"])
    wb["Notes"].append(["ships in 2 days"])
    buf = io.BytesIO()
    wb.save(buf)
    text = get_extractor("xlsx").extract(buf.getvalue()).text
    assert "## Sheet: Prices" in text and "sku: A-1 | price: 9.5" in text
    assert "## Sheet: Notes" in text and "note: ships in 2 days" in text


def test_csv_rows_drop_empty_cells():
    result = get_extractor("csv").extract(b"name,city\nAda,London\nBob,\n")
    assert result.text == "name: Ada | city: London\nname: Bob"
    assert result.metadata == {"rows": 2, "columns": ["name", "city"]}


def test_html_strips_scripts_and_chrome():
    html = (b"<html><head><title>Docs</title><script>evil()</script></head>"
            b"<body><nav>menu</nav><p>Real content</p><footer>(c)</footer></body></html>")
    result = get_extractor("html").extract(html)
    assert "Real content" in result.text
    assert not any(s in result.text for s in ("evil()", "menu", "(c)"))
    assert result.metadata == {"title": "Docs"}


def test_image_metadata_with_ocr_stubbed(monkeypatch):
    # The tesseract binary is a system dependency (present in the Docker image, not in CI),
    # so OCR itself is stubbed; Pillow's decoding is what is under test here.
    import pytesseract
    from PIL import Image

    monkeypatch.setattr(pytesseract, "image_to_string", lambda image: "  scanned text \n")
    buf = io.BytesIO()
    Image.new("RGB", (64, 32), "white").save(buf, format="PNG")
    result = get_extractor("image").extract(buf.getvalue())
    assert result.text == "scanned text"
    assert result.metadata == {"width": 64, "height": 32, "format": "PNG"}


def test_unknown_type_has_no_extractor():
    assert get_extractor("exe") is None
