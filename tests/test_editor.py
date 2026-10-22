from cryptoproof.editor import apply_approved, parse_model_edits, validate_edits


def test_approved_only_and_right_to_left_offsets():
    text = "数子签名和数字正书。"
    assert validate_edits(text) == []  # 词库不能绕过模型自动添加修改。
    edits = validate_edits(text, [
        {"start": 0, "end": 4, "original": "数子签名", "replacement": "数字签名"},
        {"start": 5, "end": 9, "original": "数字正书", "replacement": "数字证书"},
    ])
    assert len(edits) == 2
    assert apply_approved(text, edits, []) == text
    assert apply_approved(text, edits, [edits[0]["id"]]) == "数字签名和数字正书。"
    assert apply_approved(text, edits, [e["id"] for e in edits]) == "数字签名和数字证书。"


def test_wrong_offset_llm_ignored():
    text = "正确术语数字签名和数字签名"
    edits = validate_edits(text, [{"start": 0, "end": 4, "original": "数字签名",
                                 "replacement": "任意替换", "reason": "随便改"}])
    assert not edits


def test_unique_original_repairs_model_offset_without_changing_replacement():
    text = "请妥善保官文件。"
    edits = validate_edits(text, [{"start": 2, "end": 4, "original": "保官",
                                 "replacement": "保管", "reason": "错字"}])
    assert len(edits) == 1
    assert edits[0]["start"] == 3 and edits[0]["end"] == 5
    assert apply_approved(text, edits, [edits[0]["id"]]) == "请妥善保管文件。"


def test_unverified_llm_requires_review():
    text = "本段明显明显重复"
    edits = validate_edits(text, [{"start": 2, "end": 6, "original": "明显明显",
                                 "replacement": "明显", "reason": "重复用词"}])
    # 实际该重复词从位置 2 开始；匹配内容必须精确
    assert len(edits) == 1
    assert edits[0]["risk"] == "high"
    assert edits[0]["origin"] == "llm_unverified"


def test_parse_model_edits():
    value = '```json\n{"edits":[{"start":0,"end":2,"original":"坏词","replacement":"好词"}]}\n```'
    assert parse_model_edits(value)[0]["replacement"] == "好词"
