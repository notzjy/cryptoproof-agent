import json

import pytest
from langchain_core.messages import AIMessage

from cryptoproof.word_budget import count_nonspace, fit_document_budget


class Selector:
    def __init__(self, required, optional, reason="保留实验结果与限制。"):
        self.selection = {"required_ids": required, "optional_ids": optional, "reason": reason}
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return AIMessage(content=json.dumps(self.selection, ensure_ascii=False))


SENTENCES = [character * 29 + "。" for character in "甲乙丙丁戊"]
BODY = "".join(SENTENCES)


def test_selects_complete_original_sentences_and_restores_source_order():
    model = Selector(["U0001"], ["U0005", "U0003", "U0002", "U0004"])
    result = fit_document_budget(model, BODY, 100, [{"statement": "必须保留首句"}])
    assert result["body"] == SENTENCES[0] + SENTENCES[2] + SENTENCES[4]
    assert result["word_count"] == 90 and result["within_target"] and result["changed"]
    assert count_nonspace(result["body"]) == result["word_count"]
    payload = json.loads(model.calls[0][-1].content)
    assert payload["experiment_facts"] == [{"statement": "必须保留首句"}]
    assert [unit["length"] for unit in payload["units"]] == [30] * 5


def test_heading_stays_with_first_complete_sentence_and_quotes_are_not_cut():
    body = "一、实验结果\n“" + "甲" * 27 + "。”" + SENTENCES[1] + "\n\n二、结果限制\n" + SENTENCES[2] + SENTENCES[3]
    model = Selector(["U0001", "U0003"], [])
    result = fit_document_budget(model, body, 80, [])
    assert result is not None
    assert "一、实验结果\n“" + "甲" * 27 + "。”" in result["body"]
    assert "二、结果限制\n" + SENTENCES[2] in result["body"]
    assert SENTENCES[1] not in result["body"] and SENTENCES[3] not in result["body"]
    assert 68 <= result["word_count"] <= 92


def test_required_results_over_budget_or_insufficient_selection_returns_none():
    assert fit_document_budget(Selector(["U0001", "U0002", "U0003", "U0004"], []), BODY, 100, []) is None
    assert fit_document_budget(Selector(["U0001"], ["U0002"]), BODY, 100, []) is None


@pytest.mark.parametrize("required, optional", [
    ([], ["U0001"]), (["U9999"], []), (["U0001", "U0001"], []),
    (["U0001"], ["U0001"]), (["U0001"], ["U0002", "U0002"]),
    (["U0001"], [1]),
])
def test_invalid_unknown_or_duplicate_ids_cannot_change_document(required, optional):
    with pytest.raises(ValueError):
        fit_document_budget(Selector(required, optional), BODY, 100, [])


def test_decimal_points_are_preserved_and_long_sentences_are_never_sliced():
    long_sentence = "成功率为0.91，" + "甲" * 120 + "。"
    model = Selector(["U0001"], [])
    assert fit_document_budget(model, long_sentence + SENTENCES[1], 100, []) is None
    payload = json.loads(model.calls[0][-1].content)
    assert payload["units"][0]["text"] == long_sentence
    assert "0.91" in payload["units"][0]["text"]


def test_empty_body_or_invalid_target_does_not_start_model_selection():
    model = Selector(["U0001"], [])
    assert fit_document_budget(model, " \n\t", 100, []) is None
    with pytest.raises(ValueError):
        fit_document_budget(model, BODY, 0, [])
    assert model.calls == []
