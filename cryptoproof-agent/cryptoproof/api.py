"""CryptoProof Agent API. 本地演示应用；公网部署前必须补充登录和访问控制。"""
from __future__ import annotations
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import os
import sqlite3
import uuid

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from docx import Document
from langgraph.checkpoint.sqlite import SqliteSaver

from .agent import ProofreadingAgent
from .documents import extract_text, MAX_CHARS, MAX_BYTES
from .knowledge import retrieve

ROOT = Path(__file__).resolve().parent.parent


class SubmitRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)


class ApprovalRequest(BaseModel):
    approved_ids: list[str] = Field(default_factory=list, max_length=200)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=600)


def create_app(data_dir: str | Path | None = None, mode: str | None = None) -> FastAPI:
    data_path = Path(data_dir or os.getenv("DATA_DIR") or ROOT / "data")
    effective_mode = (mode or os.getenv("AGENT_MODE") or "demo").lower()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        data_path.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(data_path / "checkpoints.sqlite", check_same_thread=False)
        app.state.connection = conn
        app.state.engine = ProofreadingAgent(SqliteSaver(conn), mode=effective_mode)
        yield
        conn.close()

    app = FastAPI(title="CryptoProof Agent", version="0.1.0", lifespan=lifespan)

    def engine() -> ProofreadingAgent:
        return app.state.engine

    def result_for(task_id: str) -> dict:
        snapshot = engine().snapshot(task_id)
        if not snapshot or not snapshot.values or not snapshot.values.get("text"):
            raise HTTPException(status_code=404, detail="任务不存在")
        state = snapshot.values
        waiting = "human_review" in snapshot.next
        return {
            "task_id": task_id,
            "status": "awaiting_review" if waiting else state.get("status", "running"),
            "mode": effective_mode,
            "rag_mode": os.getenv("RAG_MODE", "sparse"),
            "original_text": state["text"],
            "corrected_text": state.get("corrected_text"),
            "edits": state.get("edits", []),
            "approved_ids": state.get("approved_ids", []),
            "trace": engine().tool_trace(task_id),
        }

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": effective_mode,
                "rag_mode": os.getenv("RAG_MODE", "sparse"), "agent": "langgraph"}

    @app.post("/api/tasks")
    def submit(request: SubmitRequest):
        if not request.text.strip():
            raise HTTPException(status_code=422, detail="内容不能为空")
        task_id = str(uuid.uuid4())
        try:
            engine().submit(request.text, task_id)
        except Exception as exc:
            # 接口错误不返回服务端密钥、请求包、文档正文；详细错误只在本地日志中排查。
            raise HTTPException(status_code=502, detail=f"校对流程执行失败：{type(exc).__name__}") from exc
        return result_for(task_id)

    @app.post("/api/tasks/upload")
    async def submit_file(file: UploadFile = File(...)):
        if file.size and file.size > MAX_BYTES:
            raise HTTPException(status_code=413, detail="文件超出 3MB 限制")
        raw = await file.read(MAX_BYTES + 1)
        try:
            text = extract_text(file.filename or "", raw)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return submit(SubmitRequest(text=text))

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        return result_for(task_id)

    @app.post("/api/tasks/{task_id}/approve")
    def approve(task_id: str, request: ApprovalRequest):
        snapshot = engine().snapshot(task_id)
        if not snapshot or "human_review" not in snapshot.next:
            raise HTTPException(status_code=409, detail="任务不在等待人工审核状态")
        known = {e["id"] for e in snapshot.values.get("edits", [])}
        if not set(request.approved_ids).issubset(known):
            raise HTTPException(status_code=422, detail="包含不属于该任务的修改项")
        try:
            engine().resume(task_id, request.approved_ids)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"审核恢复失败：{type(exc).__name__}") from exc
        return result_for(task_id)

    @app.post("/api/knowledge/search")
    def search(request: SearchRequest):
        try:
            return {"query": request.query, "results": retrieve(request.query)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"知识检索不可用：{type(exc).__name__}") from exc

    @app.get("/api/tasks/{task_id}/download.docx")
    def download_docx(task_id: str):
        result = result_for(task_id)
        if result["status"] != "completed":
            raise HTTPException(status_code=409, detail="请先完成人工审核")
        doc = Document()
        doc.add_heading("CryptoProof Agent 校对结果", 0)
        doc.add_paragraph(result["corrected_text"] or "")
        doc.add_heading("审核记录", level=1)
        for edit in result["edits"]:
            label = "通过" if edit["id"] in result["approved_ids"] else "未采纳"
            doc.add_paragraph(
                f'[{label}] {edit["original"]} → {edit["replacement"]}；'
                f'原因：{edit["reason"]}；来源：{edit["source"]}')
        content = BytesIO()
        doc.save(content)
        content.seek(0)
        return StreamingResponse(content,
                                 media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                 headers={"Content-Disposition":f'attachment; filename="cryptoproof-{task_id[:8]}.docx"'})

    @app.get("/")
    def home():
        return FileResponse(ROOT / "static" / "index.html")

    return app


app = create_app()
