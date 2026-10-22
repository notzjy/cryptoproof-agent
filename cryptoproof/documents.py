"""受限文件解析：不执行宏、不执行外部脚本；扫描 PDF 暂不支持 OCR。"""
from pathlib import Path
from io import BytesIO
from docx import Document
from pypdf import PdfReader

MAX_BYTES = 3 * 1024 * 1024
MAX_CHARS = 20000


def extract_text(filename: str, content: bytes) -> str:
    if not filename:
        raise ValueError("缺少文件名")
    if len(content) > MAX_BYTES:
        raise ValueError("文件超过 3MB 演示版限制")
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("TXT 请使用 UTF-8 编码") from exc
    elif suffix == ".docx":
        try:
            doc = Document(BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as exc:
            raise ValueError("DOCX 无法解析") from exc
    elif suffix == ".pdf":
        try:
            reader = PdfReader(BytesIO(content))
            if len(reader.pages) > 40:
                raise ValueError("PDF 不能超过 40 页")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF 无法解析") from exc
        if not text.strip():
            raise ValueError("PDF 未提取到文字：扫描件 OCR 尚未实现")
    else:
        raise ValueError("只支持 .txt、.docx、.pdf")
    if not text.strip():
        raise ValueError("文件没有可供校对的文字")
    if len(text) > MAX_CHARS:
        raise ValueError(f"文本超过 {MAX_CHARS} 字符；演示版暂不支持长文档分批处理")
    return text
