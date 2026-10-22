"""SQLite-backed reference library, experiment assets, and durable writing jobs."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .research_documents import REFERENCE_SUFFIXES, parse_research_file


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata(record: dict) -> dict:
    return {key: value for key, value in record.items() if key not in {"text", "images"}}


def _job_metadata(record: dict) -> dict:
    return {key: value for key, value in record.items() if key not in {"request", "references", "assets"}}


class ResearchStore:
    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "research.sqlite"
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            for table in ("research_references", "research_assets", "research_generations"):
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _insert(self, table: str, record: dict) -> None:
        with self._connection() as db:
            db.execute(f"INSERT INTO {table} (id,created_at,payload) VALUES (?,?,?)",
                       (record["id"], record["created_at"], json.dumps(record, ensure_ascii=False)))

    def _get(self, table: str, identity: str) -> dict:
        with self._connection() as db:
            row = db.execute(f"SELECT payload FROM {table} WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row["payload"])

    def add_reference(self, title: str, filename: str, raw: bytes) -> dict:
        title = title.strip()
        if not title or len(title) > 180:
            raise ValueError("资料名称不能为空，且不能超过 180 字符。")
        if Path(filename).suffix.lower() not in REFERENCE_SUFFIXES:
            raise ValueError("参考资料只支持 TXT、Markdown、DOCX 和可提取文字的 PDF。")
        parsed = parse_research_file(filename, raw, allow_images=False)
        record = {"id": str(uuid4()), "title": title, "filename": Path(filename).name,
                  "char_count": len(parsed["text"]), "table_count": parsed["table_count"],
                  "image_count": parsed["image_count"], "created_at": _now(),
                  "preview": parsed["text"][:240], "warnings": parsed["warnings"],
                  "text": parsed["text"]}
        self._insert("research_references", record)
        return _metadata(record)

    def list_references(self) -> list[dict]:
        with self._connection() as db:
            rows = db.execute("SELECT payload FROM research_references ORDER BY created_at DESC, id DESC").fetchall()
        return [_metadata(json.loads(row["payload"])) for row in rows]

    def get_reference(self, identity: str) -> dict:
        return self._get("research_references", identity)

    def delete_reference(self, identity: str) -> None:
        with self._connection() as db:
            result = db.execute("DELETE FROM research_references WHERE id=?", (identity,))
            if not result.rowcount:
                raise KeyError(identity)

    def add_asset(self, filename: str, raw: bytes) -> dict:
        parsed = parse_research_file(filename, raw, allow_images=True)
        record = {"id": str(uuid4()), "filename": Path(filename).name, "kind": parsed["kind"],
                  "created_at": _now(), "char_count": len(parsed["text"]),
                  "table_count": parsed["table_count"], "image_count": parsed["image_count"],
                  "warnings": parsed["warnings"], "preview": parsed["text"][:240],
                  "text": parsed["text"], "images": parsed["images"]}
        self._insert("research_assets", record)
        return _metadata(record)

    def get_asset(self, identity: str) -> dict:
        return self._get("research_assets", identity)

    def create_generation(self, request: dict, references: list[dict], assets: list[dict]) -> dict:
        # JSON copy freezes selected document content even if a library item is later deleted.
        snapshot = json.loads(json.dumps({"request": request, "references": references, "assets": assets}, ensure_ascii=False))
        record = {"id": str(uuid4()), "status": "queued", "stage": "queued", "message": "已排队，准备分析实验结果与参考资料。",
                  "title": request.get("title", "") or "实验分析文档", "created_at": _now(),
                  "target_words": request.get("target_words", 1000), "word_count": 0, "within_target": False,
                  "body": "", "warnings": [], "error": "",
                  "reference_sources": [_metadata(item) for item in references],
                  "asset_sources": [_metadata(item) for item in assets], **snapshot}
        self._insert("research_generations", record)
        return record

    def get_generation(self, identity: str) -> dict:
        return self._get("research_generations", identity)

    def update_generation(self, identity: str, **fields) -> dict:
        if "id" in fields and fields["id"] != identity:
            raise ValueError("不能修改任务标识。")
        # Read and write in a single transaction so progress fields do not overwrite each other.
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM research_generations WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            record = json.loads(row["payload"])
            record.update(fields)
            db.execute("UPDATE research_generations SET payload=? WHERE id=?",
                       (json.dumps(record, ensure_ascii=False), identity))
        return record

    def list_generations(self) -> list[dict]:
        with self._connection() as db:
            rows = db.execute("SELECT payload FROM research_generations ORDER BY created_at DESC, id DESC LIMIT 20").fetchall()
        return [_job_metadata(json.loads(row["payload"])) for row in rows]

    def recover_interrupted(self) -> int:
        recovered = 0
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT id,payload FROM research_generations").fetchall():
                record = json.loads(row["payload"])
                if record.get("status") not in {"queued", "analyzing", "writing", "revising"}:
                    continue
                record.update(status="failed", stage="failed", message="服务已重启，此次生成未完成，请重新生成。",
                              error="生成期间服务重启，未自动恢复模型请求。")
                db.execute("UPDATE research_generations SET payload=? WHERE id=?",
                           (json.dumps(record, ensure_ascii=False), row["id"]))
                recovered += 1
        return recovered
