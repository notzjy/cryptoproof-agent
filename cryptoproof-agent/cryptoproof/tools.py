"""供大模型自主选择的 LangChain 工具。"""
import json
from langchain_core.tools import tool
from .knowledge import detect_known_typos, find_surface_anomalies, retrieve


@tool
def lookup_term_candidates(text: str) -> str:
    """检查用户提供的中文文本中的密码学专业术语误写，返回精确字符位置、替换候选和来源。"""
    return json.dumps(detect_known_typos(text[:22000]), ensure_ascii=False)


@tool
def search_knowledge(query: str) -> str:
    """从领域术语与审查规则知识库中检索相关证据。用户问到术语含义、规范用法、改错依据时应使用。"""
    return json.dumps(retrieve(query[:600], top_k=4), ensure_ascii=False)


@tool
def inspect_punctuation(text: str) -> str:
    """检查重复标点等表面异常，只返回线索，不擅自修改。"""
    return json.dumps(find_surface_anomalies(text[:22000]), ensure_ascii=False)


TOOLS = [lookup_term_candidates, search_knowledge, inspect_punctuation]
