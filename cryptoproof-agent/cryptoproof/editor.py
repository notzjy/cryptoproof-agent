"""与 LLM 解耦的安全修改器：严格位置验证、冲突去重、逐项授权。"""
from __future__ import annotations
import json
import re
from typing import Any
from pydantic import BaseModel, Field, ValidationError
from .knowledge import detect_known_typos


class EditProposal(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    original: str = Field(min_length=1, max_length=120)
    replacement: str = Field(max_length=120)
    reason: str = Field(default="模型建议，待人工核验", max_length=300)
    source: str = Field(default="LLM 建议/未核实", max_length=160)


def parse_model_edits(content: Any) -> list[dict]:
    if not isinstance(content, str):
        return []
    source = content.strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?\s*|\s*```$", "", source).strip()
    try:
        obj = json.loads(source)
    except json.JSONDecodeError:
        return []
    if isinstance(obj, dict):
        edits = obj.get("edits", [])
        return edits if isinstance(edits, list) else []
    return []


def validate_edits(text: str, model_edits: list[dict] | None = None) -> list[dict]:
    """领域词库提供可证明的候选；额外 LLM 修改只进入待人工审核。"""
    prepared = [{**e} for e in detect_known_typos(text)]
    for raw in (model_edits or [])[:50]:
        try:
            e = EditProposal.model_validate(raw).model_dump()
        except (ValidationError, TypeError, ValueError):
            continue
        start, end = e["start"], e["end"]
        if end > len(text) or end <= start or text[start:end] != e["original"]:
            continue
        if e["replacement"] == e["original"]:
            continue
        if "\n" in e["replacement"] or len(e["replacement"]) > 120:
            continue
        # 不信任模型自称的引用与置信度；展示的来源由程序标记。
        prepared.append({**e, "source": "LLM 建议（须人工核实）",
                         "origin": "llm_unverified", "risk": "high"})
    prepared.sort(key=lambda e: (e["start"], e["end"], e.get("risk") != "low"))
    result = []
    last_end = -1
    seen = set()
    for edit in prepared:
        key = (edit["start"], edit["end"], edit["replacement"])
        if key in seen or edit["start"] < last_end:
            continue
        seen.add(key)
        last_end = edit["end"]
        edit["id"] = f"edit-{len(result)+1:03d}"
        result.append(edit)
    return result


def apply_approved(text: str, edits: list[dict], approved_ids: list[str]) -> str:
    """只能应用本任务中的有效、互不重叠修改；原文不自动被篡改。"""
    allowed = set(approved_ids)
    result = text
    selected = [e for e in edits if e["id"] in allowed]
    selected.sort(key=lambda e: e["start"], reverse=True)
    earliest = len(text) + 1
    for e in selected:
        start, end = e["start"], e["end"]
        if end > earliest or start < 0 or end > len(text):
            raise ValueError("修改区间重叠或越界")
        if text[start:end] != e["original"]:
            raise ValueError("原文位置已变化，请重新提交")
        result = result[:start] + e["replacement"] + result[end:]
        earliest = start
    return result
