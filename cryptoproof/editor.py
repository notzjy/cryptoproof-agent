"""与 LLM 解耦的安全修改器：严格位置验证、冲突去重、逐项授权。"""
from __future__ import annotations
import json
import re
from typing import Any
from pydantic import BaseModel, Field, ValidationError


class EditProposal(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    original: str = Field(min_length=1, max_length=120)
    replacement: str = Field(max_length=120)
    reason: str = Field(default="模型建议，待人工核验", max_length=300)
    source: str = Field(default="LLM 建议/未核实", max_length=160)


def parse_model_edits(content: Any) -> list[dict]:
    """兼容旧脚本；真实 Agent 使用严格解析并向用户报告解析错误。"""
    try:
        obj = parse_model_result(content)
    except (ValueError, TypeError):
        return []
    edits = obj.get("edits", [])
    return edits if isinstance(edits, list) else []


def parse_model_result(content: Any) -> dict:
    if not isinstance(content, str):
        raise ValueError("模型没有返回文本 JSON")
    source = content.strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:json)?\s*|\s*```$", "", source).strip()
    obj = json.loads(source)
    if not isinstance(obj, dict):
        raise ValueError("模型 JSON 必须是对象")
    return obj


def validate_edits(text: str, model_edits: list[dict] | None = None,
                   diagnostics: list[str] | None = None) -> list[dict]:
    """只验证模型提出的修改，不自动添加词库规则修改。"""
    prepared = []
    diagnostics = diagnostics if diagnostics is not None else []
    if len(model_edits or []) > 200:
        diagnostics.append("单次建议超过 200 条，超出部分未处理，请分段校对。")
    for raw in (model_edits or [])[:200]:
        # 模型负责判断改什么；程序只修正可唯一定位的字符坐标。
        # 重复片段不能靠 find() 猜测要改哪一处，仍要求模型给出正确位置。
        if isinstance(raw, dict) and isinstance(raw.get("original"), str) and raw["original"]:
            original = raw["original"]
            start, end = raw.get("start"), raw.get("end")
            exact = (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                     and text[start:end] == original)
            first = text.find(original)
            if not exact and first >= 0 and text.find(original, first + 1) < 0:
                raw = {**raw, "start": first, "end": first + len(original)}
        try:
            e = EditProposal.model_validate(raw).model_dump()
        except (ValidationError, TypeError, ValueError):
            diagnostics.append("有一条模型建议格式不符合要求，未列入可采纳修改。")
            continue
        start, end = e["start"], e["end"]
        if end > len(text) or end <= start or text[start:end] != e["original"]:
            diagnostics.append(f'“{e["original"]}”：无法确定正确的原文位置，已保留原文。')
            continue
        if e["replacement"] == e["original"]:
            continue
        if "\n" in e["replacement"] or len(e["replacement"]) > 120:
            diagnostics.append(f'“{e["original"]}”：替换内容超出单条修改范围，已保留原文。')
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
        if key in seen:
            continue
        if edit["start"] < last_end:
            diagnostics.append(f'“{edit["original"]}”：与另一条修改位置重叠，已忽略重复区间的建议。')
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
