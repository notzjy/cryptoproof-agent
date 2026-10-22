"""用本地模型替身检查实验事实、参考资料隔离和生成任务状态。"""
from copy import deepcopy
import json

import pytest
from langchain_core.messages import AIMessage

import cryptoproof.generation as generation


EXPERIMENT = "第17轮实验，rk0四个字节命中1个，查询次数20480。"
REFERENCE = {
    "id": "paper-1", "title": "参考论文", "filename": "paper.docx",
    "text": "参考论文使用1000把随机主密钥，999把恢复成功。16轮恢复率66%。",
    "warnings": [],
}


class FakeStore:
    def __init__(self, request=None, assets=None, references=None):
        self.job = {
            "id": "generation-1", "status": "queued", "body": "", "warnings": [],
            "request": request or make_request().model_dump(),
            "references": deepcopy([REFERENCE] if references is None else references),
            "assets": deepcopy(assets or []),
        }
        self.updates = []

    def get_generation(self, generation_id):
        assert generation_id == "generation-1"
        return deepcopy(self.job)

    def update_generation(self, generation_id, **values):
        assert generation_id == "generation-1"
        self.updates.append(deepcopy(values))
        self.job.update(deepcopy(values))


class ScriptedModel:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if not self.answers:
            raise AssertionError("模型调用次数超出测试预期")
        answer = self.answers.pop(0)
        if isinstance(answer, AIMessage):
            return answer
        return AIMessage(content=answer if isinstance(answer, str)
                         else json.dumps(answer, ensure_ascii=False))


def make_request(**overrides):
    return generation.GenerationRequest.model_validate({
        "title": "本次实验分析", "experiment_text": EXPERIMENT,
        "target_words": 300, "reference_ids": ["paper-1"], **overrides,
    })


def evidence(facts=None):
    return {
        "summary": "单目标四个轮密钥字节的实验结果。",
        "facts": facts if facts is not None else [
            {"statement": "第17轮实验四个轮密钥字节仅命中1个。",
             "evidence_quote": "rk0四个字节命中1个"},
        ],
        "limitations": ["只有一个目标密钥，不能推广为多密钥恢复率。"],
        "conflicts": [],
    }


def draft(body=None):
    return {"title": "模型生成标题", "body": body or "分析结果。" * 60,
            "missing_information": []}


def audit(supported=True):
    return {"supported": supported,
            "issues": [] if supported else ["把字节命中表述成主密钥完整恢复。"]}


def run_job(monkeypatch, answers, request=None, assets=None, references=None):
    store = FakeStore(request=request, assets=assets, references=references)
    model = ScriptedModel(answers)
    built_for_vision = []

    def build_model(vision=False):
        built_for_vision.append(vision)
        return model

    monkeypatch.setattr(generation, "build_generation_model", build_model)
    service = generation.GenerationService(store)
    try:
        service.run("generation-1")
    finally:
        service.shutdown()
    return store, model, built_for_vision


def user_payload(call):
    return json.loads(call[-1].content)


def test_generation_extracts_writes_audits_and_completes_with_isolated_sources(monkeypatch):
    store, model, vision = run_job(monkeypatch, [evidence(), draft(), audit()])
    job = store.job
    assert job["status"] == job["stage"] == "completed"
    assert job["title"] == "本次实验分析"
    assert job["body"] == draft()["body"]
    assert job["word_count"] == 300 and job["within_target"]
    assert job["error"] is None
    assert evidence()["limitations"][0] in job["warnings"]
    assert [u["status"] for u in store.updates if "status" in u] == [
        "analyzing", "writing", "revising", "completed",
    ]
    assert len(model.calls) == 3 and vision == [False, False]

    fact_call, write_call, audit_call = model.calls
    facts_input = user_payload(fact_call)
    assert facts_input["experiment_text"] == EXPERIMENT
    for marker in ("1000", "999", "66%"):
        assert marker not in str(fact_call)
    write_input = user_payload(write_call)
    assert write_input["experiment_facts"][0]["facts"] == evidence()["facts"]
    assert write_input["reference_excerpts"][0]["id"] == "paper-1"
    assert write_input["reference_excerpts"][0]["excerpts"][0]["text"] == REFERENCE["text"]
    assert "999" not in json.dumps(write_input["experiment_facts"], ensure_ascii=False)
    assert "不得把参考论文" in write_call[0].content
    audit_input = user_payload(audit_call)
    assert set(audit_input) == {"experiment_facts", "original_evidence_excerpts", "draft"}
    assert audit_input["original_evidence_excerpts"][0]["excerpts"][0]["text"] == EXPERIMENT
    assert REFERENCE["text"] not in json.dumps(audit_input, ensure_ascii=False)
    assert "reference_excerpts" not in audit_input


@pytest.mark.parametrize("text, expected", [
    (" 中文\n测试\t。 ", 5), ("SM4 17\n轮", 6), ("", 0),
    ("字\u3000数\r\n！", 3), ("实验🙂", 3),
])
def test_character_count_excludes_whitespace_and_includes_punctuation(text, expected):
    assert generation.count_words(text) == expected


def test_length_failure_is_revised_and_recounted(monkeypatch):
    short = draft("原始短稿。" * 20)
    final = draft("修改后正文。" * 50)
    store, model, _ = run_job(monkeypatch, [evidence(), short, audit(), final, audit()])
    assert store.job["status"] == "completed"
    assert store.job["body"] == final["body"]
    assert store.job["word_count"] == generation.count_words(final["body"]) == 300
    assert store.job["within_target"]
    repair = user_payload(model.calls[3])
    assert repair["previous_draft"] == short
    assert repair["actual_word_count"] == 100
    assert repair["allowed_range"] == [255, 345]
    assert repair["target_words"] == 300


def test_two_length_repairs_keep_full_text_and_report_actual_count(monkeypatch):
    monkeypatch.setattr(generation, "fit_document_budget", lambda *args, **kwargs: None, raising=False)
    bodies = ["首稿正文。" * 100, "修订正文。" * 110, "最终正文。" * 120]
    answers = [evidence(), draft(bodies[0]), audit(), draft(bodies[1]), audit(),
               draft(bodies[2]), audit()]
    store, model, _ = run_job(monkeypatch, answers)
    assert store.job["status"] == "completed"
    assert store.job["body"] == bodies[2]  # 超长文稿不能被强制截断。
    assert store.job["word_count"] == 600
    assert not store.job["within_target"]
    assert any("600" in warning and "未截断" in warning for warning in store.job["warnings"])
    assert len(model.calls) == 7  # 1 次提取、3 次撰写和3次审核。
    assert not model.answers
    assert user_payload(model.calls[3])["actual_word_count"] == 500
    assert user_payload(model.calls[5])["actual_word_count"] == 550


def overlong_answers():
    return [evidence(), draft("首稿正文。" * 100), audit(),
            draft("修订正文。" * 110), audit(), draft("最终正文。" * 120), audit()]


def sentence_selection():
    return {"required_ids": ["U0001"],
            "optional_ids": [f"U{number:04d}" for number in range(2, 121)],
            "reason": "保留主要结果和限制，删除完整重复句子。"}


def test_sentence_budget_fits_complete_sentences_then_audits_selected_body(monkeypatch):
    from cryptoproof.word_budget import fit_document_budget
    monkeypatch.setattr(generation, "fit_document_budget", fit_document_budget, raising=False)
    store, model, _ = run_job(monkeypatch, [*overlong_answers(), sentence_selection(), audit()])
    assert store.job["status"] == "completed", store.job
    assert store.job["body"] == "最终正文。" * 60
    assert store.job["word_count"] == generation.count_words(store.job["body"]) == 300
    assert store.job["within_target"]
    assert len(model.calls) == 9 and not model.answers
    selection_payload = user_payload(model.calls[-2])
    assert selection_payload["target_words"] == 300
    assert selection_payload["allowed_range"] == [255, 345]
    assert len(selection_payload["units"]) == 120
    assert all(unit["text"] == "最终正文。" for unit in selection_payload["units"])
    assert selection_payload["experiment_facts"][0]["facts"] == evidence()["facts"]
    final_audit = user_payload(model.calls[-1])
    assert final_audit["draft"]["body"] == store.job["body"]
    assert "独立实验事实核对员" in model.calls[-1][0].content
    assert not any("超出目标" in warning for warning in store.job["warnings"])


def test_sentence_budget_failed_fact_audit_falls_back_to_prior_audited_draft(monkeypatch):
    from cryptoproof.word_budget import fit_document_budget
    monkeypatch.setattr(generation, "fit_document_budget", fit_document_budget, raising=False)
    store, model, _ = run_job(monkeypatch, [*overlong_answers(), sentence_selection(), audit(False)])
    assert store.job["status"] == "completed", store.job
    assert store.job["body"] == "最终正文。" * 120
    assert store.job["word_count"] == 600
    assert not store.job["within_target"]
    assert len(model.calls) == 9 and not model.answers
    assert user_payload(model.calls[-1])["draft"]["body"] == "最终正文。" * 60
    assert any("600" in warning and "超出目标" in warning for warning in store.job["warnings"])
    assert all(update.get("body") != "最终正文。" * 60 for update in store.updates)


@pytest.mark.parametrize("helper_result", [None, {}, {"body": []}, {"body": "太短。"},
                                           {"body": 123}, ValueError("无效选择"), RuntimeError("选择器错误")])
def test_invalid_sentence_budget_result_or_exception_keeps_audited_overlong_body(monkeypatch, helper_result):
    calls = []

    def invalid_helper(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(helper_result, Exception):
            raise helper_result
        return helper_result

    monkeypatch.setattr(generation, "fit_document_budget", invalid_helper, raising=False)
    store, model, _ = run_job(monkeypatch, overlong_answers())
    assert len(calls) == 1
    assert store.job["status"] == "completed", store.job
    assert store.job["body"] == "最终正文。" * 120
    assert store.job["word_count"] == 600 and not store.job["within_target"]
    assert any("600" in warning and "超出目标" in warning for warning in store.job["warnings"])
    assert len(model.calls) == 7 and not model.answers


def test_repeated_fact_audit_failure_never_publishes_body(monkeypatch):
    store, model, _ = run_job(monkeypatch, [
        evidence(), draft(), audit(False), draft(), audit(False), draft(), audit(False),
    ])
    assert store.job["status"] == "failed"
    assert "事实核对" in store.job["error"]
    assert store.job["body"] == ""
    assert not any("body" in update for update in store.updates)
    assert audit(False)["issues"][0] in store.job["warnings"]
    assert len(model.calls) == 7
    assert user_payload(model.calls[3])["audit_issues"] == audit(False)["issues"]


@pytest.mark.parametrize("answers", [
    ["不是 JSON"],
    [evidence(), "不是 JSON"],
    [evidence(), draft(), "不是 JSON"],
    [evidence(), draft(), {"issues": []}],
])
def test_invalid_or_incomplete_model_json_fails_without_output(monkeypatch, answers):
    store, _, _ = run_job(monkeypatch, answers)
    assert store.job["status"] == "failed"
    assert "结构化结果" in store.job["error"]
    assert store.job["body"] == ""
    assert not any("body" in update for update in store.updates)


def test_experiment_image_is_passed_to_vision_model_as_image_url(monkeypatch):
    image = {"filename": "result.png", "mime_type": "image/png", "data": "dGVzdA=="}
    assets = [{"filename": "result.png", "text": "", "images": [image], "warnings": []}]
    request = make_request(experiment_text="", asset_ids=["image-1"]).model_dump()
    report = evidence([{"statement": "图中一组实验字节命中1/4。", "evidence_quote": "1/4"}])
    store, model, vision = run_job(monkeypatch, [report, draft(), audit()], request, assets)
    assert store.job["status"] == "completed"
    assert vision == [True, False]
    blocks = model.calls[0][-1].content
    assert blocks[0]["type"] == "text"
    assert json.loads(blocks[0]["text"])["experiment_text"] == ""
    assert blocks[-1] == {"type": "image_url", "image_url": {
        "url": "data:image/png;base64,dGVzdA==",
    }}


def test_unmatched_fact_quote_is_excluded_before_writing(monkeypatch):
    valid = evidence()["facts"][0]
    unsupported = {"statement": "完整恢复了1000把主密钥。", "evidence_quote": "成功1000次"}
    store, model, _ = run_job(monkeypatch, [evidence([valid, unsupported]), draft(), audit()])
    assert store.job["status"] == "completed"
    facts = user_payload(model.calls[1])["experiment_facts"][0]["facts"]
    assert facts == [valid]
    assert any("引文对应" in warning for warning in store.job["warnings"])
    assert "成功1000次" not in json.dumps(user_payload(model.calls[2]), ensure_ascii=False)


def test_all_unmatched_quotes_fail_before_writing(monkeypatch):
    unsupported = {"statement": "完整恢复了1000把主密钥。", "evidence_quote": "成功1000次"}
    store, model, _ = run_job(monkeypatch, [evidence([unsupported])])
    assert store.job["status"] == "failed"
    assert "可核验" in store.job["error"]
    assert len(model.calls) == 1 and store.job["body"] == ""


@pytest.mark.parametrize("overrides,assets,references,message", [
    ({"experiment_text": " \n\t"}, [], [REFERENCE], "实验结果"),
    ({"reference_ids": ["paper-1", "paper-1"]}, [], [REFERENCE], "重复"),
    ({"asset_ids": ["asset-1", "asset-1"]}, [], [REFERENCE], "重复"),
    ({}, [{"filename": "many.docx", "text": "", "images": [{}] * 13}], [REFERENCE], "12"),
    ({}, [], [], "参考资料"),
    ({}, [{"filename": "huge.txt", "text": "字" * 120001}], [REFERENCE], "120000"),
])
def test_invalid_inputs_rejected_before_any_model_call(monkeypatch, overrides, assets, references, message):
    request = make_request(**overrides)
    with pytest.raises(generation.GenerationInputError, match=message):
        generation.validate_generation_inputs(request, references, assets)
    store, model, vision = run_job(monkeypatch, [], request.model_dump(), assets, references)
    assert store.job["status"] == "failed" and message in store.job["error"]
    assert model.calls == [] and vision == []


def test_model_length_limit_is_failure_and_keeps_draft_private(monkeypatch):
    limited = AIMessage(content=json.dumps(draft(), ensure_ascii=False),
                        response_metadata={"finish_reason": "length"})
    store, model, _ = run_job(monkeypatch, [evidence(), limited])
    assert store.job["status"] == "failed"
    assert "长度上限" in store.job["error"]
    assert store.job["body"] == "" and len(model.calls) == 2


def test_long_references_are_bounded_and_include_source_locations():
    references = [{**REFERENCE, "text": "参考开头。" * 800 + "特定实验描述。" * 800}]
    excerpts = generation.reference_excerpts(references, "特定实验描述", total_budget=2000)
    assert excerpts[0]["id"] == REFERENCE["id"]
    pieces = excerpts[0]["excerpts"]
    assert sum(len(item["text"]) for item in pieces) <= 2000
    assert pieces[0]["location"].startswith("字符 0")
    for piece in pieces:
        assert piece["text"] in references[0]["text"]


def test_generation_model_config_json_mode_and_vision_inherit_generation(monkeypatch):
    captured = []
    for prefix in ("OPENAI", "GENERATION", "VISION"):
        for suffix in ("MODEL", "BASE_URL", "API_KEY"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "fallback-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://fallback.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-fallback-key")
    monkeypatch.setenv("GENERATION_MODEL", "deepseek-chat")
    monkeypatch.setenv("GENERATION_BASE_URL", "https://generation.invalid/v1")
    monkeypatch.setenv("GENERATION_API_KEY", "test-generation-key")
    marker = object()

    def capture(**options):
        captured.append(options)
        return marker

    monkeypatch.setattr(generation, "ChatOpenAI", capture)
    assert generation.build_generation_model() is marker
    assert generation.build_generation_model(vision=True) is marker
    assert len(captured) == 2 and captured[0] == captured[1]
    options = captured[0]
    assert options["model"] == "deepseek-chat"
    assert options["base_url"] == "https://generation.invalid/v1"
    assert options["api_key"] == "test-generation-key"
    assert options["model_kwargs"] == {"response_format": {"type": "json_object"}}
    assert options["extra_body"] == {"thinking": {"type": "disabled"}}
    assert options["temperature"] == 0 and options["timeout"] == 180


def test_generation_model_config_vision_overrides_generation_credentials_and_endpoint(monkeypatch):
    captured = []
    for prefix in ("OPENAI", "GENERATION", "VISION"):
        for suffix in ("MODEL", "BASE_URL", "API_KEY"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-fallback-key")
    monkeypatch.setenv("GENERATION_MODEL", "generation-model")
    monkeypatch.setenv("GENERATION_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("GENERATION_API_KEY", "test-generation-key")
    monkeypatch.setenv("VISION_MODEL", "image-capable-model")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.invalid/v1")
    monkeypatch.setenv("VISION_API_KEY", "test-vision-key")
    monkeypatch.setattr(generation, "ChatOpenAI", lambda **options: captured.append(options))
    generation.build_generation_model()
    generation.build_generation_model(vision=True)
    text, vision = captured
    assert text["model"] == "generation-model"
    assert text["api_key"] == "test-generation-key"
    assert text["base_url"] == "https://api.deepseek.com"
    assert text["extra_body"] == {"thinking": {"type": "disabled"}}
    assert vision["model"] == "image-capable-model"
    assert vision["api_key"] == "test-vision-key"
    assert vision["base_url"] == "https://vision.invalid/v1"
    assert "extra_body" not in vision
    for options in captured:
        assert options["model_kwargs"] == {"response_format": {"type": "json_object"}}
