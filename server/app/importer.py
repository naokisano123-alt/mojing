"""TXT/Markdown 导入：解码、规范化、分块。

契约：docs/contracts/M1_AB_API.md 第 5 节。
所有 `start_char` / `end_char` 单位均为 UTF-16 code unit（与浏览器 JS 字符串一致）。
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass

from .config import Settings
from .errors import ApiError

TARGET_CHUNK_UNITS = 1800
HARD_CHUNK_UNITS = 4000
PREVIEW_UNITS = 160


def u16len(text: str) -> int:
    """UTF-16 code unit 长度。"""
    return len(text.encode("utf-16-le")) // 2


def _u16_of(ch: str) -> int:
    return 2 if ord(ch) > 0xFFFF else 1


def decode_document(data: bytes, settings: Settings) -> tuple[str, str]:
    """返回 (正文, 实际编码)。失败抛 ApiError，绝不静默产生替代字符。"""
    if not data:
        raise ApiError(422, "EMPTY_DOCUMENT", "文件是空的。")

    utf16_boms = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
    if b"\x00" in data and not data.startswith(utf16_boms):
        raise ApiError(
            415,
            "BINARY_CONTENT",
            "这个文件看起来是二进制内容，不是文本文件。",
        )

    bom_table = (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    )
    for bom, encoding in bom_table:
        if data.startswith(bom):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError as exc:
                raise ApiError(
                    422,
                    "ENCODING_UNSUPPORTED",
                    f"文件带有 {encoding} 的 BOM，但内容不是合法的 {encoding}。",
                    {"tried": [encoding], "hint": "请另存为 UTF-8 后重新导入。"},
                ) from exc

    candidates = ["utf-8"]
    if settings.allow_gb18030:
        candidates.append("gb18030")
    if settings.allow_big5:
        candidates.append("big5")

    tried: list[str] = []
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            tried.append(encoding)

    raise ApiError(
        422,
        "ENCODING_UNSUPPORTED",
        "无法可靠识别这段文本的编码，已拒绝导入以免出现乱码。",
        {
            "tried": tried,
            "hint": "请在电脑端把文件另存为 UTF-8，然后重新导入。",
        },
    )


def normalize(text: str) -> str:
    """换行归一 + 去 BOM；保留作者排版，不裁剪首尾空白。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text


# ---------------------------------------------------------------- 分块

_ATX = re.compile(r"^(#{1,6})[ \t]+(\S.*?)\s*$")
_CN_CHAPTER = re.compile(r"^第[0-9一二三四五六七八九十百千零〇两]+[卷章回节篇部]")
_SENTENCE_END = "。！？；…\n"


@dataclass
class Segment:
    start: int  # code point 起点
    end: int  # code point 终点（不含）
    kind: str  # "heading" | "para"
    level: int = 1
    title: str = ""


@dataclass
class ChunkData:
    chunk_index: int
    heading_path: list[str]
    start_char: int
    end_char: int
    text: str

    @property
    def text_length(self) -> int:
        return self.end_char - self.start_char


def _iter_lines(text: str):
    pos = 0
    length = len(text)
    while pos < length:
        nl = text.find("\n", pos)
        if nl == -1:
            yield pos, length, text[pos:length]
            return
        yield pos, nl + 1, text[pos:nl]
        pos = nl + 1


def _is_fence(stripped: str) -> bool:
    return stripped.startswith("```") or stripped.startswith("~~~")


def _segments(text: str) -> list[Segment]:
    segments: list[Segment] = []
    para_start: int | None = None
    in_fence = False
    fence_token = ""

    for start, end, line in _iter_lines(text):
        stripped = line.strip()

        if in_fence:
            if stripped.startswith(fence_token):
                in_fence = False
            continue

        if _is_fence(stripped):
            in_fence = True
            fence_token = stripped[:3]
            if para_start is None:
                para_start = start
            continue

        if not stripped:
            if para_start is not None:
                segments.append(Segment(para_start, start, "para"))
                para_start = None
            continue

        atx = _ATX.match(line)
        if atx:
            if para_start is not None:
                segments.append(Segment(para_start, start, "para"))
                para_start = None
            segments.append(
                Segment(start, end, "heading", len(atx.group(1)), atx.group(2).strip())
            )
            continue

        if len(stripped) <= 30 and _CN_CHAPTER.match(stripped):
            if para_start is not None:
                segments.append(Segment(para_start, start, "para"))
                para_start = None
            segments.append(Segment(start, end, "heading", 1, stripped))
            continue

        if para_start is None:
            para_start = start

    if para_start is not None:
        segments.append(Segment(para_start, len(text), "para"))

    return segments


def _paths(segments: list[Segment]) -> list[list[str]]:
    """每段结束时的标题栈快照。"""
    stack: list[tuple[int, str]] = []
    out: list[list[str]] = []
    for seg in segments:
        if seg.kind == "heading":
            while stack and stack[-1][0] >= seg.level:
                stack.pop()
            stack.append((seg.level, seg.title))
        out.append([title for _, title in stack])
    return out


def _cut_cp(text: str, start: int, max_units: int) -> int:
    """从 start 起最多 max_units 个 UTF-16 单位的 code point 终点。"""
    units = 0
    i = start
    n = len(text)
    while i < n:
        u = _u16_of(text[i])
        if units + u > max_units:
            break
        units += u
        i += 1
    return i


def _split_long_range(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """把超过硬上限的区间按句末标点软切；找不到标点才硬切。"""
    ranges: list[tuple[int, int]] = []
    pos = start
    while end - pos > 0:
        if u16len(text[pos:end]) <= HARD_CHUNK_UNITS:
            ranges.append((pos, end))
            break
        cut = _cut_cp(text, pos, HARD_CHUNK_UNITS)
        window = max(pos + 1, cut - 200)
        soft = -1
        for i in range(cut, window - 1, -1):
            if text[i - 1] in _SENTENCE_END:
                soft = i
                break
        if soft > pos:
            cut = soft
        ranges.append((pos, cut))
        pos = cut
    return ranges


def chunk_text(text: str) -> list[ChunkData]:
    """确定性分块（无模型），保证覆盖不变式见契约 §3.4。"""
    if not text:
        return []
    segments = _segments(text)
    if not segments:
        return []

    paths = _paths(segments)
    index_of_start = {seg.start: i for i, seg in enumerate(segments)}

    bounds: list[int] = [0]
    for i, seg in enumerate(segments):
        if i == 0:
            continue
        if seg.kind == "heading":
            if seg.start > bounds[-1]:
                bounds.append(seg.start)
            continue
        next_start = segments[i + 1].start if i + 1 < len(segments) else len(text)
        if u16len(text[bounds[-1]:next_start]) > TARGET_CHUNK_UNITS and seg.start > bounds[-1]:
            bounds.append(seg.start)

    ranges: list[tuple[int, int]] = []
    for i, start in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(text)
        if end > start:
            ranges.extend(_split_long_range(text, start, end))

    chunks: list[ChunkData] = []
    cursor = 0
    for index, (start, end) in enumerate(ranges):
        body = text[start:end]
        seg_index = index_of_start.get(start)
        if seg_index is None:
            seg_index = 0
            while seg_index < len(segments) and segments[seg_index].start < start:
                seg_index += 1
            seg_index = min(seg_index, len(segments) - 1)
        chunks.append(
            ChunkData(
                chunk_index=index,
                heading_path=list(paths[seg_index]),
                start_char=cursor,
                end_char=cursor + u16len(body),
                text=body,
            )
        )
        cursor += u16len(body)

    return chunks


def preview(text: str, limit: int = PREVIEW_UNITS) -> str:
    """前 limit 个 UTF-16 单位，不切断代理对，不加省略号。"""
    units = 0
    i = 0
    for ch in text:
        u = _u16_of(ch)
        if units + u > limit:
            break
        units += u
        i += 1
    return text[:i]


def cp_at_u16(text: str, target: int) -> int:
    """把 UTF-16 偏移换算成 Python 字符串下标。"""
    if target <= 0:
        return 0
    units = 0
    for i, ch in enumerate(text):
        if units >= target:
            return i
        units += _u16_of(ch)
    return len(text)


def slice_u16(text: str, offset: int, limit: int) -> tuple[str, int]:
    """按 UTF-16 偏移切片，返回 (片段, 实际长度单位)。"""
    start = cp_at_u16(text, offset)
    end = cp_at_u16(text, offset + limit)
    body = text[start:end]
    return body, u16len(body)
