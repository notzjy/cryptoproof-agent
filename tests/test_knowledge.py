from cryptoproof.knowledge import detect_known_typos, lexical_retrieve, find_surface_anomalies


def test_domain_typos_and_offsets():
    text = "通过数子签名验证数字正书。数子签名需要复查。"
    edits = detect_known_typos(text)
    assert len(edits) == 3
    assert all(text[x["start"]:x["end"]] == x["original"] for x in edits)
    assert [x["replacement"] for x in edits] == ["数字签名", "数字证书", "数字签名"]


def test_correct_terms_not_changed():
    assert detect_known_typos("数字签名、数字证书和密钥管理") == []


def test_rag_returns_traceable_source():
    hits = lexical_retrieve("数字签名")
    assert hits
    assert any("数字签名" in e["content"] for e in hits)
    assert all(e["source"] and e["id"] and e["retriever"] == "char_tfidf" for e in hits)


def test_punctuation_check_only_reports():
    assert find_surface_anomalies("错误，，请检查。")
