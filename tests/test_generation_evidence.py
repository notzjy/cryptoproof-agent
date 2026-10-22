"""Regression checks for evidence isolation and stopping background writing work."""
import json

import cryptoproof.generation as generation
from test_generation import (
    FakeStore, ScriptedModel, audit, draft, evidence, make_request, run_job, user_payload,
)


def test_rejected_fact_cannot_return_through_summary_or_unverified_notes(monkeypatch):
    valid = evidence()["facts"][0]
    rejected = {"statement": "攻击成功1000次。", "evidence_quote": "成功1000次"}
    report = {
        **evidence([valid, rejected]),
        "summary": "攻击成功1000次。",
        "limitations": ["尚待核实：攻击成功1000次。"],
        "conflicts": ["成功1000次与成功999次互相冲突。"],
    }
    store, model, _ = run_job(monkeypatch, [report, draft(), audit()])
    assert store.job["status"] == "completed"
    for call in model.calls[1:]:
        verified = user_payload(call)["experiment_facts"]
        assert "1000" not in json.dumps(verified, ensure_ascii=False)
        assert "999" not in json.dumps(verified, ensure_ascii=False)
        assert verified[0]["facts"] == [valid]
    assert "1000" not in store.job["fact_summary"]
    assert report["limitations"][0] in store.job["warnings"]
    assert report["conflicts"][0] in store.job["warnings"]


def test_whitespace_quote_cannot_authorize_an_unsupported_fact(monkeypatch):
    valid = evidence()["facts"][0]
    unsupported = {"statement": "完整恢复一千把主密钥。", "evidence_quote": " \n\t\u3000"}
    store, model, _ = run_job(monkeypatch, [evidence([valid, unsupported]), draft(), audit()])
    assert store.job["status"] == "completed"
    assert user_payload(model.calls[1])["experiment_facts"][0]["facts"] == [valid]
    assert "完整恢复一千把" not in json.dumps(user_payload(model.calls[2]), ensure_ascii=False)
    assert any("引文对应" in warning for warning in store.job["warnings"])


def test_picture_in_same_attachment_does_not_bypass_text_quote_verification(monkeypatch):
    valid = evidence()["facts"][0]
    unsupported = {"statement": "完整恢复一千把主密钥。", "evidence_quote": "成功一千次"}
    image_fact = {"statement": "图中查询数为20480。", "evidence_quote": "queries=20480"}
    image = {"filename": "plot.png", "mime_type": "image/png", "data": "dGVzdA=="}
    assets = [{"filename": "results.docx", "text": "rk0四个字节命中1个。", "images": [image], "warnings": []}]
    request = make_request(experiment_text="", asset_ids=["evidence-1"]).model_dump()
    store, model, vision = run_job(monkeypatch,
        [evidence([valid, unsupported]), evidence([image_fact]), draft(), audit()], request, assets)
    assert store.job["status"] == "completed"
    assert vision == [False, True, False]
    assert isinstance(model.calls[0][-1].content, str)
    image_payload = model.calls[1][-1].content
    assert json.loads(image_payload[0]["text"])["experiment_text"] == ""
    assert image_payload[-1]["type"] == "image_url"
    for call in model.calls[2:]:
        verified = user_payload(call)["experiment_facts"]
        assert verified[0]["facts"] == [valid]
        assert verified[1]["facts"] == [image_fact]
        assert "完整恢复一千把" not in json.dumps(verified, ensure_ascii=False)


def test_stopped_service_never_begins_model_work(monkeypatch):
    store = FakeStore()
    def unexpected_model(*args, **kwargs):
        raise AssertionError("关闭后不得发起模型工作")
    monkeypatch.setattr(generation, "build_generation_model", unexpected_model)
    service = generation.GenerationService(store)
    service.shutdown()
    service.run("generation-1")
    assert store.job["status"] == "failed"
    assert "正在停止" in store.job["error"]
    assert store.job["body"] == ""


def test_stop_during_evidence_call_prevents_following_write_call(monkeypatch):
    store = FakeStore()
    service = generation.GenerationService(store)
    class StopAfterEvidence(ScriptedModel):
        def invoke(self, messages):
            answer = super().invoke(messages)
            service.stopping.set()
            return answer
    model = StopAfterEvidence([evidence()])
    monkeypatch.setattr(generation, "build_generation_model", lambda **kwargs: model)
    try:
        service.run("generation-1")
    finally:
        service.shutdown()
    assert len(model.calls) == 1
    assert store.job["status"] == "failed"
    assert "正在停止" in store.job["error"]
    assert store.job["body"] == ""
