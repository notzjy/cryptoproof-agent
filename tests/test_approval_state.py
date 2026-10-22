"""审核状态回归：使用真实图、工具和导出接口，模型由本地替身提供。"""
from io import BytesIO
import json

import pytest
from docx import Document
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from cryptoproof.api import create_app


TEXT = "请妥善保官文件，采用非对称加蜜。"


def proposal(original, replacement):
    start = TEXT.index(original)
    return {"start": start, "end": start + len(original),
            "original": original, "replacement": replacement,
            "reason": "测试模型的修改建议"}


class LocalModel:
    """替身主动尝试绕过术语保留决定，验证程序仍执行该决定。"""

    def __init__(self, behavior):
        self.behavior = behavior
        self.tool_messages = []
        self.calls = 0

    def drafts(self):
        edits = [proposal("保官", "保管")]
        if self.behavior == "keep_original":
            edits.append(proposal("非对称加蜜", "非对称加密"))
        return edits

    def bind_tools(self, tools):
        backend = self

        class Main:
            def invoke(self, messages):
                backend.calls += 1
                if backend.behavior == "bad_json":
                    return AIMessage(content="这不是最终结果 JSON")
                found = [message for message in messages if isinstance(message, ToolMessage)]
                backend.tool_messages = found
                if not found or backend.behavior == "no_final":
                    suspected = ("非对称加蜜" if backend.behavior == "keep_original"
                                 else "原文中不存在的疑似术语")
                    return AIMessage(content="", tool_calls=[{
                        "name": "review_domain_term", "args": {"suspected_text": suspected},
                        "id": f"review-{backend.calls}", "type": "tool_call"}])
                return AIMessage(content=json.dumps(
                    {"edits": backend.drafts(), "summary": "本地测试初步建议"}, ensure_ascii=False))

        return Main()

    def invoke(self, messages):
        payload = json.loads(messages[-1].content)
        if "candidate" in payload:
            result = {"grammar": "severe", "meaning_preserved": False,
                      "domain_fit": False, "improves_original": False,
                      "reason": "测试中没有通过术语评估的候选"}
        elif "evaluations" in payload:
            result = {"selected_term_id": None, "reason": "测试中保留原术语片段"}
        else:
            # 最终组合复核也尝试加入被禁止的替换，不能仅靠首轮过滤。
            result = {"edits": self.drafts(), "summary": "本地测试最终复核"}
        return AIMessage(content=json.dumps(result, ensure_ascii=False))


def install_model(tmp_path, monkeypatch, behavior):
    path = tmp_path / "terms.txt"
    path.write_text("非对称加密\n", encoding="utf-8")
    monkeypatch.setenv("TERMS_FILE", str(path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-no-network")
    monkeypatch.setenv("AGENT_MAX_TURNS", "2")
    backend = LocalModel(behavior)
    monkeypatch.setattr("cryptoproof.agent.ChatOpenAI", lambda **kwargs: backend)
    return backend


def submit(client):
    response = client.post("/api/tasks", json={"text": TEXT})
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, task, ids):
    response = client.post(f'/api/tasks/{task["task_id"]}/approve', json={"approved_ids": ids})
    assert response.status_code == 200, response.text
    return response.json()


def download_text(client, task_id):
    response = client.get(f"/api/tasks/{task_id}/download.docx")
    assert response.status_code == 200, response.text
    return "\n".join(paragraph.text for paragraph in Document(BytesIO(response.content)).paragraphs)


def test_filtered_term_does_not_block_other_approved_edits(tmp_path, monkeypatch):
    backend = install_model(tmp_path, monkeypatch, "keep_original")
    with TestClient(create_app(tmp_path / "db", mode="llm")) as client:
        task = submit(client)
        assert task["status"] == "awaiting_review"
        assert task["analysis_complete"] is True
        assert [edit["original"] for edit in task["edits"]] == ["保官"]
        assert any("非对称加蜜" in warning and "保留原文" in warning for warning in task["warnings"])
        tool_result = json.loads(backend.tool_messages[-1].content)
        assert tool_result["keep_original"] is True
        assert tool_result["evaluations"] and tool_result["proposed_edit"] is None

        done = approve(client, task, [edit["id"] for edit in task["edits"]])
        assert done["status"] == "completed" and done["review_completed"] is True
        assert done["corrected_text"] == TEXT.replace("保官", "保管")
        assert done["warnings"] == task["warnings"]
        assert done["corrected_text"] in download_text(client, task["task_id"])


def test_tool_failure_still_allows_approval_and_scoped_export(tmp_path, monkeypatch):
    backend = install_model(tmp_path, monkeypatch, "tool_error")
    with TestClient(create_app(tmp_path / "db", mode="llm")) as client:
        task = submit(client)
        assert any(message.status == "error" for message in backend.tool_messages)
        assert task["status"] == "awaiting_review" and task["analysis_complete"] is False
        assert task["warnings"] and len(task["edits"]) == 1

        done = approve(client, task, [task["edits"][0]["id"]])
        assert done["status"] == "completed" and done["review_completed"] is True
        assert done["analysis_complete"] is False
        assert done["corrected_text"] == TEXT.replace("保官", "保管")
        exported = download_text(client, task["task_id"])
        assert "校对范围与保留说明" in exported
        assert "未完整完成" in exported and "用户确认" in exported
        assert all(warning in exported for warning in task["warnings"])


@pytest.mark.parametrize("behavior", ["keep_original", "tool_error"])
def test_reject_all_finishes_even_with_warnings(tmp_path, monkeypatch, behavior):
    install_model(tmp_path, monkeypatch, behavior)
    with TestClient(create_app(tmp_path / "db", mode="llm")) as client:
        task = submit(client)
        done = approve(client, task, [])
        assert done["status"] == "completed" and done["review_completed"] is True
        assert done["approved_ids"] == [] and done["corrected_text"] == TEXT
        assert TEXT in download_text(client, task["task_id"])


def test_legacy_finished_approval_is_readable_and_downloadable(tmp_path, monkeypatch):
    install_model(tmp_path, monkeypatch, "keep_original")
    app = create_app(tmp_path / "db", mode="llm")
    with TestClient(app) as client:
        task = submit(client)
        approved = [task["edits"][0]["id"]]
        done = approve(client, task, approved)
        engine = app.state.engine
        config = {"configurable": {"thread_id": task["task_id"]}}
        engine.graph.update_state(config, {
            "status": "incomplete", "analysis_complete": False, "review_completed": False,
        }, as_node="apply_approved")
        snapshot = engine.snapshot(task["task_id"])
        assert not snapshot.next
        assert snapshot.values["approved_ids"] == approved
        assert snapshot.values["corrected_text"] == done["corrected_text"]

        response = client.get(f'/api/tasks/{task["task_id"]}')
        assert response.status_code == 200, response.text
        legacy = response.json()
        assert legacy["status"] == "completed" and legacy["review_completed"] is True
        assert legacy["warnings"] and legacy["analysis_complete"] is False
        assert done["corrected_text"] in download_text(client, task["task_id"])


@pytest.mark.parametrize("behavior", ["no_final", "bad_json"])
def test_unreviewed_without_final_result_cannot_export(tmp_path, monkeypatch, behavior):
    install_model(tmp_path, monkeypatch, behavior)
    with TestClient(create_app(tmp_path / "db", mode="llm")) as client:
        task = submit(client)
        assert task["status"] == "incomplete" and task["analysis_complete"] is False
        assert task["review_completed"] is False and task["approved_ids"] == []
        assert task["warnings"]
        response = client.get(f'/api/tasks/{task["task_id"]}/download.docx')
        assert response.status_code == 409
