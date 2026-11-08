"""UTF-16 偏移计算、代理对边界校验与摘要生成工具。

契约与规范（M1_R3_API.md §1.1、§1.2）：
1. 所有 start_char 与 end_char 均为文档全文 UTF-16 code unit；
2. 边界检查：禁止切断代理对（4 字节 Emoji 如 🐉 占用 2 个 UTF-16 code units）；
3. 服务端生成真实摘录：截取 [start_char, end_char) 对应文本，<=120 个 UTF-16 单位，超长两端平滑截断并追加 '…'。
"""

from __future__ import annotations


def u16len(text: str) -> int:
    """计算字符串对应的 UTF-16 code unit 长度（与浏览器 JS 保持一致）。"""
    return len(text.encode("utf-16-le")) // 2


def _u16_of(ch: str) -> int:
    return 2 if ord(ch) > 0xFFFF else 1


def check_utf16_offset(text: str, offset: int) -> bool:
    """校验 offset 是否精确落在 UTF-16 字符边界上。

    若 offset 位于代理对中间（高低代理对之间），或为负数、或超出文本 UTF-16 长度，返回 False。
    """
    if offset < 0:
        return False
    u16_cur = 0
    for ch in text:
        if u16_cur == offset:
            return True
        if u16_cur > offset:
            return False
        u16_cur += _u16_of(ch)
    return u16_cur == offset


def u16_to_cp(text: str, offset: int) -> int:
    """将合法的 UTF-16 偏移转换为 Python 字符串（码点）下标。"""
    if offset <= 0:
        return 0
    u16_cur = 0
    for i, ch in enumerate(text):
        if u16_cur >= offset:
            return i
        u16_cur += _u16_of(ch)
    return len(text)


def generate_excerpt(
    chunk_text: str,
    rel_start: int,
    rel_end: int,
    max_units: int = 120,
) -> str:
    """从块文本中截取指定区间的文字生成引用摘录。

    依据 M1_R3_API.md §1.2：
    取 [rel_start, rel_end) 对应文本，<=120 个 UTF-16 单位，超长截断并追加 '…'。
    """
    cp_start = u16_to_cp(chunk_text, rel_start)
    cp_end = u16_to_cp(chunk_text, rel_end)
    target = chunk_text[cp_start:cp_end]

    if u16len(target) <= max_units:
        return target

    # 超出 120 单位时，取前 max_units - 1 单位并安全截断（不切代理对），加 '…'
    target_limit = max_units - 1
    units = 0
    cut_idx = 0
    for ch in target:
        u = _u16_of(ch)
        if units + u > target_limit:
            break
        units += u
        cut_idx += 1

    return target[:cut_idx] + "…"
