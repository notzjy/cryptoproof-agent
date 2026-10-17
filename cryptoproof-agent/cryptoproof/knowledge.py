"""可追溯知识检索：离线字符 TF-IDF + 可选真实 Embedding 语义向量。

注：本项目示例知识全部为人工整理的公开常见术语；不代表官方标准文本。
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
import json
import math
import os
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def terms() -> list[dict[str, Any]]:
    return json.loads((ROOT / "knowledge" / "glossary.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def rules() -> list[dict[str, Any]]:
    return json.loads((ROOT / "knowledge" / "rules.json").read_text(encoding="utf-8"))


def knowledge_documents() -> list[dict[str, str]]:
    docs = []
    for term in terms():
        docs.append({
            "id": term["id"], "source": term["source"],
            "content": f'{term["term"]}：{term["definition"]}。常见误写：{", ".join(term["incorrect_variants"])}。',
        })
    for rule in rules():
        docs.append({"id": rule["id"], "source": rule["source"],
                     "content": f'{rule["title"]}：{rule["text"]}'})
    return docs


def ngrams(text: str) -> list[str]:
    text = "".join(c for c in text.lower() if not c.isspace())
    if not text:
        return []
    # 字符 1/2/3-gram，兼顾中文错别字与英文缩写
    return [text[i:i+n] for n in (1, 2, 3) for i in range(max(0, len(text)-n+1))]


def _tfidf_vectors(documents: list[str], query: str) -> tuple[list[dict], dict]:
    sequences = [Counter(ngrams(s)) for s in [*documents, query]]
    corpus = sequences[:-1]
    df = Counter()
    for freq in corpus:
        df.update(freq.keys())
    total = len(corpus)

    def vector(counts: Counter) -> dict[str, float]:
        weights = {k: (1 + math.log(v)) * (math.log((total+1)/(df.get(k, 0)+1))+1)
                   for k, v in counts.items()}
        norm = math.sqrt(sum(v*v for v in weights.values())) or 1.0
        return {k: w/norm for k, w in weights.items()}
    return [vector(d) for d in corpus], vector(sequences[-1])


def lexical_retrieve(query: str, top_k: int = 4) -> list[dict]:
    docs = knowledge_documents()
    vecs, query_vec = _tfidf_vectors([d["content"] for d in docs], query)
    scored = []
    for doc, v in zip(docs, vecs):
        sim = sum(w * v.get(k, 0.0) for k, w in query_vec.items())
        if sim > 0:
            scored.append({**doc, "score": round(sim, 4), "retriever": "char_tfidf"})
    return sorted(scored, key=lambda x: x["score"], reverse=True)[:top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return sum(x*y for x, y in zip(a, b)) / (
        (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b))) or 1.0
    )


def semantic_retrieve(query: str, top_k: int = 4) -> list[dict]:
    """真实 embedding 模型（付费 API）和 SQLite 向量持久化；适合小规模知识库。"""
    from langchain_openai import OpenAIEmbeddings

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("RAG_MODE=semantic 必须配置 OPENAI_API_KEY")
    embeddings = OpenAIEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
    )
    db_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "vectors.sqlite"
    documents = knowledge_documents()
    model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS vectors (id TEXT, model TEXT, content TEXT, vector TEXT, PRIMARY KEY (id, model))")
        known = {row[0]: row[1] for row in connection.execute(
            "SELECT id, content FROM vectors WHERE model=?", (model_name,))}
        missing = [doc for doc in documents if known.get(doc["id"]) != doc["content"]]
        if missing:
            vecs = embeddings.embed_documents([doc["content"] for doc in missing])
            connection.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                                  [(doc["id"], model_name, doc["content"], json.dumps(vec))
                                   for doc, vec in zip(missing, vecs)])
            connection.commit()
        rows = list(connection.execute("SELECT id, content, vector FROM vectors WHERE model=?", (model_name,)))
    qvec = embeddings.embed_query(query)
    by_id = {doc["id"]: doc for doc in documents}
    ranked = sorted((({**by_id[rid], "score": round(_cosine(qvec, json.loads(v)), 4),
                         "retriever": "openai_embeddings"}) for rid, _, v in rows if rid in by_id),
                    key=lambda x: x["score"], reverse=True)
    return ranked[:top_k]


def retrieve(query: str, top_k: int = 4) -> list[dict]:
    if not query.strip():
        return []
    top_k = min(max(top_k, 1), 10)
    if os.getenv("RAG_MODE", "sparse").lower() == "semantic":
        return semantic_retrieve(query, top_k)
    return lexical_retrieve(query, top_k)


def detect_known_typos(text: str) -> list[dict]:
    """领域规则候选；使用精确偏移，默认绝不自动应用。"""
    candidates: list[dict] = []
    for entry in terms():
        for wrong in entry["incorrect_variants"]:
            pos = text.find(wrong)
            while pos != -1:
                candidates.append({
                    "start": pos, "end": pos+len(wrong),
                    "original": wrong, "replacement": entry["term"],
                    "reason": f'专业术语疑似误写，规范候选：{entry["term"]}',
                    "source": entry["source"], "risk": "low", "origin": "glossary_rule",
                })
                pos = text.find(wrong, pos+len(wrong))
    # 最长匹配优先：避免“非对称加蜜”又被当作子串“对称加蜜”重复报错。
    candidates.sort(key=lambda x: (x["start"], -(x["end"] - x["start"])))
    selected: list[dict] = []
    for item in candidates:
        if any(item["start"] < prev["end"] and item["end"] > prev["start"] for prev in selected):
            continue
        selected.append(item)
    return sorted(selected, key=lambda x: x["start"])


def find_surface_anomalies(text: str) -> list[dict]:
    """可作为 Agent 工具的非模型化辅助检测：重复标点/文字只报告、不直接修改。"""
    hits = []
    for i in range(len(text)-1):
        if text[i] == text[i+1] and text[i] in "，。！？；：":
            hits.append({"offset": i, "context": text[max(i-8, 0):i+10],
                         "issue": "连续重复标点，建议复核"})
    return hits
