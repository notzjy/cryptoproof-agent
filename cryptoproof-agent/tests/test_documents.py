from io import BytesIO
from docx import Document
import pytest
from cryptoproof.documents import extract_text


def test_text_extract():
    assert extract_text("x.txt", "中文文本".encode()) == "中文文本"


def test_docx_extract():
    doc = Document()
    doc.add_paragraph("数子签名")
    b = BytesIO()
    doc.save(b)
    assert "数子签名" in extract_text("sample.docx", b.getvalue())


def test_disallowed_files():
    with pytest.raises(ValueError):
        extract_text("a.exe", b"test")
