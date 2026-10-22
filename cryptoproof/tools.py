"""供大模型自主选择的 LangChain 工具。"""
import json
from typing import Annotated
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from .knowledge import detect_known_typos, find_surface_anomalies, retrieve
from .term_review import review_term


def build_review_tools(reviewer, top_k: int = 5):
    """reviewer 不绑定工具，避免候选评估递归调用自身。"""
    @tool
    def review_domain_term(suspected_text: str, state: Annotated[dict, InjectedState],
                           occurrence: int = 0) -> str:
        """当你自主判断原文某片段疑似当前所选领域的专业术语误写、需要核实时调用。
        suspected_text 必须逐字来自原文；occurrence 是该片段第几次出现，从 0 开始。
        工具检索正确术语，逐个让模型评估替换后的原上下文，再让模型比较择优或保留原文。
        普通错别字、语病、标点与语义问题由你直接检查，不需要调用此工具。
        返回全部候选评估、选择理由，以及含精确原文位置的 proposed_edit。
        """
        return json.dumps(review_term(state["text"], suspected_text, occurrence, reviewer, top_k,
                                      library=state.get("library")),
                          ensure_ascii=False)

    return [review_domain_term]


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


# 旧工具只用于离线演示，不提供给真实 LLM 校对流程。
TOOLS = [lookup_term_candidates, search_knowledge, inspect_punctuation]
