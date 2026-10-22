"""术语库管理。库选择属于单个任务，库内容随任务保存快照。"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

from .knowledge import terms

MAX_LIBRARY_BYTES = 1024 * 1024
MAX_LIBRARY_TERMS = 20000


def parse_terms_file(raw: bytes, filename: str) -> list[dict]:
    if not filename.lower().endswith(".txt"):
        raise ValueError("请上传 TXT 格式的术语文件")
    if len(raw) > MAX_LIBRARY_BYTES:
        raise ValueError("术语文件不能超过 1MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("请将文件保存为 UTF-8 编码后上传") from exc
    records, seen = [], set()
    source_name = filename.replace("\\", "/").split("/")[-1]
    for number, line in enumerate(content.splitlines(), 1):
        term = line.strip()
        if not term or term in seen:
            continue
        if len(term) > 120 or any(ord(char) < 32 for char in term):
            raise ValueError(f"第 {number} 行格式不正确：每行一个术语，最多 120 字")
        seen.add(term)
        records.append({"id": f"term-{number:06d}", "term": term, "definition": "",
                        "incorrect_variants": [], "source": f"{source_name} 第 {number} 行"})
        if len(records) > MAX_LIBRARY_TERMS:
            raise ValueError("单个术语库最多支持 20000 个术语")
    if not records:
        raise ValueError("文件中没有有效术语，请每行填写一个术语")
    return records


class LibraryStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "libraries.sqlite"
        data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS libraries (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, domain TEXT NOT NULL,
                filename TEXT NOT NULL, created_at TEXT NOT NULL, terms_json TEXT NOT NULL)""")
            connection.execute("CREATE TABLE IF NOT EXISTS library_settings (key TEXT PRIMARY KEY, value TEXT)")
            if not connection.execute("SELECT 1 FROM library_settings WHERE key='initialized'").fetchone():
                default = terms()
                connection.execute("INSERT INTO libraries VALUES (?, ?, ?, ?, ?, ?)",
                    ("default-crypto", "密码术语库", "密码学", "crypto_terms_zh.txt", self.now(),
                     json.dumps(default, ensure_ascii=False)))
                connection.execute("INSERT INTO library_settings VALUES ('initialized', '1')")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def metadata(row):
        records = json.loads(row["terms_json"])
        return {key: row[key] for key in ("id", "name", "domain", "filename", "created_at")} | {
            "term_count": len(records), "preview": [record["term"] for record in records[:6]]}

    def list(self):
        with self.connect() as connection:
            return [self.metadata(row) for row in connection.execute("SELECT * FROM libraries ORDER BY created_at, id")]

    def snapshot(self, library_id: str | None = None):
        with self.connect() as connection:
            row = (connection.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
                   if library_id else connection.execute("SELECT * FROM libraries ORDER BY created_at, id LIMIT 1").fetchone())
            if row is None:
                raise KeyError("术语库不存在或已删除，请重新选择或添加术语库")
            return {**self.metadata(row), "terms": json.loads(row["terms_json"])}

    def add(self, name: str, domain: str, filename: str, raw: bytes):
        name, domain = name.strip(), domain.strip()
        if not name or len(name) > 60 or len(domain) > 60 or any(ord(char) < 32 for char in name + domain):
            raise ValueError("请输入 1～60 字的术语库名称，领域名称最多 60 字")
        records = parse_terms_file(raw, filename)
        library_id = uuid.uuid4().hex
        for record in records:
            record["id"] = f'{library_id}/{record["id"]}'
        try:
            with self.connect() as connection:
                connection.execute("INSERT INTO libraries VALUES (?, ?, ?, ?, ?, ?)",
                    (library_id, name, domain or name, filename.replace("\\", "/").split("/")[-1],
                     self.now(), json.dumps(records, ensure_ascii=False)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("已存在同名术语库，请换一个名称") from exc
        return {k: v for k, v in self.snapshot(library_id).items() if k != "terms"}

    def delete(self, library_id: str):
        with self.connect() as connection:
            deleted = connection.execute("DELETE FROM libraries WHERE id=?", (library_id,))
            if not deleted.rowcount:
                raise KeyError("术语库不存在或已删除")
