"""FTS5 / Trigram 全文检索可选方案与分析。

供集成方（DeepSeekHarness）评估与选择：
1. 现状与挑战：
   - SQLite 默认内置的 FTS5 使用 unicode61 分词器，按空格及西文标点切词，对中文无分词效果（中文连续文本会被当作单个巨大 token 或无法匹配任意子串）。
   - SQLite 支持 `tokenize = "trigram"`（3-gram）分词器，但需要 SQLite >= 3.34 且编译时启用了 FTS5 trigram 支持。
   - Trigram 对 >= 3 个字符的中文查询有效；但对于 1 个字（如“陆”、“刀”）或 2 个字（如“旧街”、“红枫”）的短词，FTS5 trigram 无法直接通过常规 MATCH 进行高效索引检索，仍需退化或特殊处理。
2. 双轨策略建议（若引入 FTS5）：
   - 保持 `instr` 作为 1–2 字短词及无 FTS5 扩展环境下的兜底与基线方案。
   - 若查询词 >= 3 字符且系统支持 FTS5 trigram，可由 DSH 开启 FTS 表加速。
3. 索引同步与软删除事务策略：
   - 如果创建单独的虚表 `chunks_fts`，在 document / world 软删除或硬删除时，必须使用 SQLite 触发器（Trigger）或在主业务事务中同步清理 `chunks_fts`，避免已删除文档的幽灵命中。
"""

from __future__ import annotations

import sqlite3


def check_fts5_available(conn: sqlite3.Connection) -> bool:
    """探测当前 SQLite 环境是否支持 FTS5 及 trigram 分词。"""
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _test_fts USING fts5(content, tokenize='trigram')"
        )
        cur.execute("INSERT INTO _test_fts(content) VALUES ('测试中文文本')")
        cur.execute("SELECT rowid FROM _test_fts WHERE _test_fts MATCH '中文'")
        cur.fetchone()
        cur.execute("DROP TABLE _test_fts")
        return True
    except Exception:
        return False
