"""M3 第四轮（Wave 4）导入设定与 AI 初始化草案暂存 Schema（供独立测试与集成方按需挂载）。

说明：
- 根据 `M3_WAVE4_GEMINI_MARCIN.md`：「交付资料抽取、提示词、输出校验和草案适配层。提交位置、数据库写入与模型会话交给集成方。」
- 本模块提供轻量、幂等的 `ensure_world_analysis_schema(conn)`（不隐式提交外层事务），
  用于在单元测试或集成时验证「未确认草案不写入正式事件、人物、知识与时间；重试、重开和分支可区分；确认后原子落库」。
"""

from __future__ import annotations

import sqlite3

WORLD_ANALYSIS_SCHEMA_VERSION = "1.0-m3-wave4"

WORLD_ANALYSIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS m3_world_analysis_drafts (
    draft_id                    TEXT PRIMARY KEY,
    world_id                    TEXT NOT NULL,
    instance_id                 TEXT NOT NULL,
    adventure_id                TEXT,
    request_id                  TEXT NOT NULL,
    run_mode                    TEXT NOT NULL DEFAULT 'initial' CHECK(run_mode IN ('initial', 'retry', 'reopen', 'branch')),
    status                      TEXT NOT NULL DEFAULT 'ready_to_confirm' CHECK(status IN ('waiting', 'insufficient_sources', 'analyzing', 'ready_to_confirm', 'failed_retryable', 'applied')),
    starting_location           TEXT NOT NULL,
    world_minute                INTEGER NOT NULL DEFAULT 0,
    base_world_revision         INTEGER NOT NULL DEFAULT 0,
    analysis_fingerprint        TEXT NOT NULL DEFAULT '',
    parent_draft_id             TEXT,
    branch_from_checkpoint_id   TEXT,
    draft_json                  TEXT NOT NULL DEFAULT '{}',
    player_summary_json         TEXT NOT NULL DEFAULT '{}',
    error_code                  TEXT,
    error_message               TEXT,
    attempt_count               INTEGER NOT NULL DEFAULT 1,
    created_at                  REAL NOT NULL,
    updated_at                  REAL NOT NULL,
    applied_at                  REAL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_m3_world_analysis_req
    ON m3_world_analysis_drafts(world_id, instance_id, request_id);

CREATE INDEX IF NOT EXISTS idx_m3_world_analysis_lookup
    ON m3_world_analysis_drafts(world_id, instance_id, status, updated_at DESC);
"""


def _split_sql_statements(sql_script: str) -> list[str]:
    statements: list[str] = []
    for raw in sql_script.split(";"):
        stmt = raw.strip()
        if stmt:
            statements.append(stmt)
    return statements


def ensure_world_analysis_schema(conn: sqlite3.Connection) -> None:
    """幂等创建分析草案暂存表，不使用 `executescript`，绝不隐式提交外层事务或破坏 SAVEPOINT。"""
    for stmt in _split_sql_statements(WORLD_ANALYSIS_SCHEMA):
        conn.execute(stmt)
