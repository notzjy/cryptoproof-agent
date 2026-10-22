"""用模型替身验证流程，不访问付费 API，也不把替身结果当作模型准确率。"""
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from cryptoproof.editor import parse_model_result
from cryptoproof.knowledge import terms, search_term_candidates
from cryptoproof.term_review import review_term


def term_file(tmp_path, monkeypatch, text="数字签名\n数字证书\n数字信封\n"):
    path = tmp_path / "terms.txt"
    path.write_text(text, encoding="utf-8-sig")
    monkeypatch.setenv("TERMS_FILE", str(path))
    return path


class Reviewer:
    def __init__(self, accepted="数字签名", choose_rejected=False):
        self.accepted = accepted
        self.choose_rejected = choose_rejected
        self.payloads = []

    def invoke(self, messages):
        payload = json.loads(messages[-1].content)
        self.payloads.append(payload)
        if "candidate" in payload:
            fits = payload["candidate"]["term"] == self.accepted
            result = {"grammar": "good" if fits else "severe", "meaning_preserved": fits,
                      "domain_fit": fits, "improves_original": fits, "reason": "测试模型评估"}
        elif "evaluations" in payload:
            eligible = [e for e in payload["evaluations"] if e["eligible"] or self.choose_rejected]
            result = {"selected_term_id": eligible[0]["term_id"] if eligible else None,
                      "reason": "测试模型择优或保留"}
        else:
            result = {"edits": payload["edits"], "summary": "最终组合复核完成"}
        return AIMessage(content=json.dumps(result, ensure_ascii=False))


def test_txt_load_deduplicate_and_reload(tmp_path, monkeypatch):
    path = term_file(tmp_path, monkeypatch, "数字签名\n\n数字签名\n 数字证书 \n")
    assert [r["term"] for r in terms()] == ["数字签名", "数字证书"]
    assert terms()[1]["source"].endswith("第 4 行")
    path.write_text("密钥\n", encoding="utf-8")
    assert [r["term"] for r in terms()] == ["密钥"]


def test_unknown_typo_retrieved_without_wrong_variants(tmp_path, monkeypatch):
    term_file(tmp_path, monkeypatch)
    hits = search_term_candidates("数子签明")
    assert any(e["term"] == "数字签名" for e in hits)
    assert all(e["incorrect_variants"] == [] for e in hits)
    assert search_term_candidates("数字签名")[0]["match_type"] == "exact"


def test_every_candidate_independent_context_and_model_selection(tmp_path, monkeypatch):
    term_file(tmp_path, monkeypatch)
    reviewer = Reviewer()
    text = "使用数子签明验证文件完整性。"
    result = review_term(text, "数子签明", 0, reviewer)
    candidates = search_term_candidates("数子签明")
    assert len(result["evaluations"]) == len(candidates) == 3
    assert len(reviewer.payloads) == len(candidates) + 1
    for item in reviewer.payloads[:-1]:
        assert item["original_context"] == text
        assert item["trial_context"] == text.replace("数子签明", item["candidate"]["term"])
    assert result["selected_term"] == "数字签名"
    assert result["proposed_edit"]["start"] == 2


def test_all_rejected_keeps_original(tmp_path, monkeypatch):
    term_file(tmp_path, monkeypatch)
    result = review_term("使用数子签明。", "数子签明", 0, Reviewer(accepted=None))
    assert result["evaluations"]
    assert result["keep_original"] and result["proposed_edit"] is None


def test_rejected_candidate_cannot_be_selected(tmp_path, monkeypatch):
    term_file(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="不合格"):
        review_term("数子签明", "数子签明", 0, Reviewer(None, choose_rejected=True))


def test_no_candidates_and_repeated_occurrence(tmp_path, monkeypatch):
    term_file(tmp_path, monkeypatch)
    reviewer = Reviewer()
    result = review_term("某某某", "某某某", 0, reviewer)
    assert not reviewer.payloads and result["keep_original"]
    text = "数子签明和数子签明"
    result = review_term(text, "数子签明", 1, reviewer)
    assert result["start"] == 5
    with pytest.raises(ValueError, match="不存在"):
        review_term(text, "数子签明", 2, reviewer)


def test_malformed_assessment_is_not_treated_as_no_error(tmp_path, monkeypatch):
    term_file(tmp_path, monkeypatch)
    class Broken:
        def invoke(self, messages):
            return AIMessage(content="这不是 JSON")
    with pytest.raises(ValueError):
        review_term("数子签明", "数子签明", 0, Broken())


def test_real_graph_tool_injection_review_and_resume(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cryptoproof.api import create_app
    term_file(tmp_path, monkeypatch)
    reviewer = Reviewer()
    class Main:
        def invoke(self, messages):
            if not any(getattr(m, "type", "") == "tool" for m in messages):
                return AIMessage(content="", tool_calls=[{"name": "review_domain_term",
                    "args": {"suspected_text": "数子签明"}, "id": "call-review", "type": "tool_call"}])
            result = parse_model_result(messages[-1].content)
            return AIMessage(content=json.dumps({"edits": [result["proposed_edit"]], "summary": "已核实术语"}))
    reviewer.bind_tools = lambda tools: Main()
    monkeypatch.setenv("OPENAI_API_KEY", "test-no-network")
    monkeypatch.setattr("cryptoproof.agent.ChatOpenAI", lambda **kwargs: reviewer)
    with TestClient(create_app(tmp_path / "db", mode="llm")) as client:
        response = client.post("/api/tasks", json={"text": "使用数子签明验证文件。"})
        assert response.status_code == 200, response.text
        task = response.json()
        assert task["status"] == "awaiting_review", task
        assert task["analysis_complete"], task
        assert len(task["edits"]) == 1
        result = next(e["result"] for e in task["trace"] if e.get("result"))
        assert len(result["evaluations"]) == 3
        done = client.post(f'/api/tasks/{task["task_id"]}/approve',
                           json={"approved_ids": [task["edits"][0]["id"]]}).json()
        assert done["corrected_text"] == "使用数字签名验证文件。"


def test_ordinary_typo_needs_no_tool(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from cryptoproof.api import create_app
    term_file(tmp_path, monkeypatch)
    reviewer = Reviewer()
    class Main:
        def invoke(self, messages):
            return AIMessage(content=json.dumps({"edits": [{"start": 3, "end": 5,
                "original": "保官", "replacement": "保管"}], "summary": "普通错字纠正"}))
    reviewer.bind_tools = lambda tools: Main()
    monkeypatch.setenv("OPENAI_API_KEY", "test-no-network")
    monkeypatch.setattr("cryptoproof.agent.ChatOpenAI", lambda **kwargs: reviewer)
    with TestClient(create_app(tmp_path / "db", mode="llm")) as client:
        task = client.post("/api/tasks", json={"text": "请妥善保官文件。"}).json()
        assert task["analysis_complete"], task
        assert task["edits"][0]["replacement"] == "保管"
        assert task["trace"] == []
        assert len(reviewer.payloads) == 1  # 只有组合复核，没有术语评估。


def test_guard_keeps_rejected_term_and_blocks_unreviewed_alternative():
    from cryptoproof.agent import ProofreadingAgent
    edit = {"start": 0, "end": 4, "original": "数子签明", "replacement": "数字证书"}
    review = {"start": 0, "end": 4, "proposed_edit": None}
    assert ProofreadingAgent.guard_term_edits([edit], [review]) == []
    review["proposed_edit"] = {**edit, "replacement": "数字签名"}
    assert ProofreadingAgent.guard_term_edits([edit], [review]) == []


@pytest.mark.parametrize("loop_forever", [False, True])
def test_tool_failure_or_round_limit_is_incomplete(tmp_path, monkeypatch, loop_forever):
    from fastapi.testclient import TestClient
    from cryptoproof.api import create_app
    term_file(tmp_path, monkeypatch)
    reviewer = Reviewer()
    class Main:
        def invoke(self, messages):
            if loop_forever or not any(getattr(m, "type", "") == "tool" for m in messages):
                return AIMessage(content="", tool_calls=[{"name": "review_domain_term",
                    "args": {"suspected_text": "原文没有这个片段"}, "id": "bad-call", "type": "tool_call"}])
            return AIMessage(content='{"edits":[],"summary":"无建议"}')
    reviewer.bind_tools = lambda tools: Main()
    monkeypatch.setenv("OPENAI_API_KEY", "test-no-network")
    monkeypatch.setenv("AGENT_MAX_TURNS", "2")
    monkeypatch.setattr("cryptoproof.agent.ChatOpenAI", lambda **kwargs: reviewer)
    with TestClient(create_app(tmp_path / "db", mode="llm")) as client:
        task = client.post("/api/tasks", json={"text": "正常文本"}).json()
        assert task["status"] == "incomplete", task
        assert not task["analysis_complete"] and task["warnings"]
