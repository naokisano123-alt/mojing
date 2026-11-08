import pytest

from app.config import Settings
from app.errors import ApiError
from app.importer import (
    chunk_text,
    decode_document,
    normalize,
    preview,
    slice_u16,
    u16len,
)

SETTINGS = Settings()


def test_u16len_counts_surrogates():
    assert u16len("abc") == 3
    assert u16len("雾") == 1
    assert u16len("\U0001F600") == 2


def test_preview_never_splits_surrogate_pair():
    text = "\U0001F600" * 100
    assert preview(text, 5) == "\U0001F600\U0001F600"
    assert preview(text, 4) == "\U0001F600\U0001F600"


def test_normalize_crlf_and_bom():
    assert normalize("\ufeff甲\r\n乙\r丙") == "甲\n乙\n丙"


def test_decode_utf8_and_gb18030():
    text = "雾港来信"
    assert decode_document(text.encode("utf-8"), SETTINGS) == (text, "utf-8")
    assert decode_document(text.encode("gb18030"), SETTINGS) == (text, "gb18030")


def test_decode_rejects_binary():
    with pytest.raises(ApiError) as exc:
        decode_document(b"abc\x00def", SETTINGS)
    assert exc.value.code == "BINARY_CONTENT"


def test_decode_rejects_undecodable():
    with pytest.raises(ApiError) as exc:
        decode_document(b"\xff\xff\xff", SETTINGS)
    assert exc.value.code == "ENCODING_UNSUPPORTED"


def test_chunk_offsets_are_utf16_based():
    text = "# 标题\n\n" + "\U0001F600" * 10 + "正文。\n"
    chunks = chunk_text(text)
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == u16len(text)
    assert "".join(chunk.text for chunk in chunks) == text


def test_long_paragraph_is_soft_split():
    paragraph = "。" * 3000 + "\n"
    text = "# 起\n\n" + paragraph
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == text


def test_slice_u16_roundtrip():
    text = "雾" * 100
    body, length = slice_u16(text, 10, 20)
    assert body == "雾" * 20
    assert length == 20

def test_long_paragraph_keeps_own_heading_path():
    """长段被切分后，每个子块仍应归属原标题，而不是下一个标题。"""
    body = ("雾" * 1200 + "。") * 4  # 约 4800 字符，触发硬切
    text = "# 第一卷\n\n" + body + "\n\n## 第二卷\n\n短段落。\n"
    chunks = chunk_text(text)
    assert len(chunks) >= 3
    first_heading_chunks = [c for c in chunks if "雾" in c.text]
    assert first_heading_chunks, "期望存在正文块"
    for chunk in first_heading_chunks:
        assert chunk.heading_path == ["第一卷"], chunk.heading_path
    second = [c for c in chunks if "短段落" in c.text]
    assert second and second[0].heading_path == ["第一卷", "第二卷"]
    assert "".join(c.text for c in chunks) == text


def test_utf16_slice_never_splits_surrogate():
    text = "甲" * 5 + "\U0001F600" * 3 + "乙" * 5
    # 从 emoji 中间开始切：offset=5 落在 emoji 起点，limit=1 只取半个代理对时也不能输出半个字符
    body, length = slice_u16(text, 5, 1)
    assert body == "\U0001F600" or body == ""
    full, full_len = slice_u16(text, 0, 100)
    assert full == text and full_len == u16len(text)
