"""M3 第四轮（Wave 4）世界资料抽取、按起点相关性排序、长文分批与合并策略。

契约依据（`docs/collaboration/M3_WAVE4_GEMINI_MARCIN.md` & `docs/contracts/M3_WAVE4_API.md`）：
1. 接收现有世界的已确认设定（`lore_entries.status='confirmed'`）和导入资料（`documents` + `chunks`），按起点挑选相关节选。
2. 检查来源只属于该世界：拒绝跨世界来源（`403 CROSS_WORLD_SOURCE_FORBIDDEN`）、已软删除或未确认来源（`422 SOURCE_FABRICATED`）。
3. 源文本是供分析的资料，文内指令不具有系统权限：对文内伪造系统指令/越权改账本指令做剥离与标记。
4. 设计长文分批与合并策略，显式给出每次请求的输入范围（`BatchInputRange`，含覆盖的 `source_ids`、文档分块索引区间与字符偏移区间）。
5. 合并多批提取结果时，自动合并同人异名/跨批复用人物（`aliases`），消歧同名不同人（`homonym_disambiguator`），并保留设定冲突（`SettingConflictItem`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
import sqlite3
from typing import Any

from .errors import (
    AnalysisSourceRequiredError,
    AnalysisValidationError,
    CrossWorldSourceError,
    SourceFabricatedError,
)
from .types import (
    ANALYSIS_ENTRY_TYPE_PRIORITY,
    DEFAULT_BATCH_CHAR_BUDGET,
    DEFAULT_BATCH_MAX_CHUNKS,
    DEFAULT_BATCH_MAX_ENTRIES,
    DEFAULT_MAX_BATCHES,
    DEFAULT_MAX_CHUNK_CHARS,
    DEFAULT_MAX_ENTRY_CHARS,
    DEFAULT_TOTAL_CHAR_BUDGET,
    BatchInputRange,
    CanonExcerptItem,
    DocumentSpanRange,
    ExtractionBatch,
    SettingConflictItem,
    StartingPointSpec,
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:忽略|无视|跳过)(?:之前|以上|前面|所有)(?:的)?(?:所有)?(?:指令|规则|约束|设定|系统提示)[^。！？\n]*[。！？\n]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|rules|prompts)[^.\n]*[.\n]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:【\s*(?:系统指令|管理员指令|最高权限|SYSTEM\s*OVERRIDE|ADMIN\s*DIRECTIVE)\s*】|<system_override>.*?</system_override>)[^。！？\n]*",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:直接修改|立刻写入|强制设置)\s*(?:玩家账本|player_ledger|coins|inventory|stamina)\s*[=:：][^\n。！？]*",
        re.IGNORECASE,
    ),
)


def u16_units(text: str, field_name: str = "text") -> int:
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise AnalysisValidationError(
            "INVALID_TEXT_ENCODING",
            f"字段 {field_name} 包含不完整的 UTF-16 代理对字符。",
            {"field": field_name},
        ) from exc


def safe_u16_truncate(text: str, max_units: int) -> str:
    if max_units <= 0:
        return ""
    if u16_units(text, "text") <= max_units:
        return text
    units = 0
    chars: list[str] = []
    for ch in text:
        ch_units = 2 if ord(ch) > 0xFFFF else 1
        if units + ch_units > max_units:
            break
        chars.append(ch)
        units += ch_units
    return "".join(chars)


def sanitize_untrusted_source_text(raw_text: str) -> tuple[str, bool]:
    """剥离源文本内试图冒充系统指令的提示词注入语句，确保源文本仅作为受分析资料。"""
    if not isinstance(raw_text, str):
        return "", False
    cleaned = raw_text
    filtered = False
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            filtered = True
            cleaned = pattern.sub(" [已隔离文内非设定指令] ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned, filtered


def normalize_starting_point(
    raw: StartingPointSpec | Mapping[str, Any] | str,
    *,
    default_world_minute: int = 0,
) -> StartingPointSpec:
    """将输入起点参数归一化为 `StartingPointSpec`。"""
    if isinstance(raw, StartingPointSpec):
        if not isinstance(raw.location, str) or not raw.location.strip():
            raise AnalysisValidationError("STARTING_LOCATION_REQUIRED", "开局起点 location 不能为空。")
        if type(raw.world_minute) is not int or raw.world_minute < 0:
            raise AnalysisValidationError("INVALID_WORLD_MINUTE", "开局时间 world_minute 必须为非负整数。")
        return raw

    if isinstance(raw, str):
        loc = raw.strip()
        if not loc:
            raise AnalysisValidationError("STARTING_LOCATION_REQUIRED", "开局起点 location 不能为空。")
        return StartingPointSpec(location=loc, world_minute=default_world_minute)

    if not isinstance(raw, Mapping):
        raise AnalysisValidationError("INVALID_STARTING_POINT", "starting_point 必须为 StartingPointSpec、字典或地点字符串。")

    loc = str(raw.get("location") or raw.get("starting_location") or raw.get("starting_point") or "").strip()
    if not loc:
        raise AnalysisValidationError("STARTING_LOCATION_REQUIRED", "必须指定非空的开局起点 location。")

    raw_min = raw.get("world_minute", default_world_minute)
    if type(raw_min) is not int or raw_min < 0:
        raise AnalysisValidationError("INVALID_WORLD_MINUTE", "world_minute 必须为非负整数。")

    starts_at = str(raw.get("starts_at") or "0001-01-01T08:00").strip()
    opening_id = str(raw["opening_id"]).strip() if raw.get("opening_id") else None
    char_name = str(raw.get("character_name") or "旅人").strip() or "旅人"

    raw_kw = raw.get("focus_keywords") or ()
    if isinstance(raw_kw, str):
        keywords = tuple(k.strip() for k in re.split(r"[,，、\s]+", raw_kw) if k.strip())
    elif isinstance(raw_kw, Sequence):
        keywords = tuple(str(k).strip() for k in raw_kw if str(k).strip())
    else:
        keywords = ()

    run_mode = str(raw.get("run_mode") or "initial").strip()
    if run_mode not in ("initial", "retry", "reopen", "branch"):
        raise AnalysisValidationError(
            "INVALID_RUN_MODE",
            f"不支持的 run_mode: {run_mode!r}，仅支持 initial/retry/reopen/branch。",
        )

    req_id = str(raw.get("request_id") or "").strip()
    parent_draft = str(raw["parent_draft_id"]).strip() if raw.get("parent_draft_id") else None
    branch_cp = (
        str(raw["branch_from_checkpoint_id"]).strip()
        if raw.get("branch_from_checkpoint_id")
        else None
    )

    return StartingPointSpec(
        location=loc,
        world_minute=raw_min,
        starts_at=starts_at,
        opening_id=opening_id,
        character_name=char_name,
        focus_keywords=keywords,
        run_mode=run_mode,  # type: ignore[arg-type]
        request_id=req_id,
        parent_draft_id=parent_draft,
        branch_from_checkpoint_id=branch_cp,
    )


def _extract_search_tokens(starting_point: StartingPointSpec) -> list[tuple[str, int]]:
    tokens: list[tuple[str, int]] = []
    seen: set[str] = set()

    loc = starting_point.location.strip()
    if loc:
        tokens.append((loc, 60))
        seen.add(loc)
        stem = re.sub(
            r"(?:水驿|驿站|学院|外院|内院|机关城|山脉|商道|谷地|镇|城|关|谷|山|港|堡|院|阁|宗|国|都|站|区|岛|境|渡|洲)$",
            "",
            loc,
        )
        if len(stem) >= 2 and stem not in seen:
            tokens.append((stem, 35))
            seen.add(stem)

    for kw in starting_point.focus_keywords:
        clean_kw = kw.strip()
        if clean_kw and clean_kw not in seen:
            tokens.append((clean_kw, 30))
            seen.add(clean_kw)

    return tokens


def _score_source_relevance(
    *,
    title: str,
    heading_path: str,
    text: str,
    kind: str,
    entry_type: str,
    chunk_index: int | None,
    search_tokens: Sequence[tuple[str, int]],
) -> tuple[int, tuple[str, ...]]:
    score = 0
    matched: list[str] = []
    combined_header = f"{title} {heading_path}"

    for token, weight in search_tokens:
        in_header = token in combined_header
        in_body = token in text
        if in_header or in_body:
            matched.append(token)
            if in_header:
                score += weight + 25
            if in_body:
                occurrences = min(3, text.count(token))
                score += weight + (occurrences - 1) * 8

    if kind == "entry":
        prio = ANALYSIS_ENTRY_TYPE_PRIORITY.get(entry_type.lower(), 8)
        score += max(5, 30 - prio * 4)
    else:
        if chunk_index == 0:
            score += 12
        elif chunk_index == 1:
            score += 6

    return score, tuple(matched)


def discover_world_source_ownership(
    conn: sqlite3.Connection,
    world_id: str,
) -> dict[str, Any]:
    """查询指定世界与同库其它世界的来源归属表，用于拦截跨世界、已删除或未确认的越界引用。"""
    if not isinstance(world_id, str) or not world_id.strip():
        raise AnalysisValidationError("WORLD_ID_REQUIRED", "world_id 不能为空。")
    clean_world_id = world_id.strip()

    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    valid_entry_ids: set[str] = set()
    valid_chunk_ids: set[str] = set()
    valid_doc_ids: set[str] = set()
    unconfirmed_or_deleted_ids: set[str] = set()
    other_world_sources: dict[str, str] = {}

    if "lore_entries" in tables:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(lore_entries)").fetchall()}
        status_col = "status" if "status" in cols else "'confirmed'"
        del_col = "deleted_at" if "deleted_at" in cols else "NULL"
        rows = conn.execute(
            f"SELECT id, world_id, {status_col}, {del_col} FROM lore_entries"
        ).fetchall()
        for r in rows:
            eid = f"entry:{r[0]}"
            w_id = str(r[1] or "")
            st = str(r[2] or "confirmed")
            deleted_at = r[3]
            if w_id != clean_world_id:
                other_world_sources[eid] = w_id
            elif deleted_at is not None or st != "confirmed":
                unconfirmed_or_deleted_ids.add(eid)
            else:
                valid_entry_ids.add(eid)

    if "documents" in tables and "chunks" in tables:
        doc_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        status_col = "d.status" if "status" in doc_cols else "'ready'"
        del_col = "d.deleted_at" if "deleted_at" in doc_cols else "NULL"
        rows = conn.execute(
            f"""
            SELECT c.id, c.world_id, d.id, d.world_id, {status_col}, {del_col}
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            """
        ).fetchall()
        for r in rows:
            cid = f"chunk:{r[0]}"
            c_world = str(r[1] or r[3] or "")
            doc_id = str(r[2] or "")
            st = str(r[4] or "ready")
            deleted_at = r[5]
            if c_world != clean_world_id:
                other_world_sources[cid] = c_world
                if doc_id:
                    other_world_sources[f"doc:{doc_id}"] = c_world
            elif deleted_at is not None or (st and st != "ready"):
                unconfirmed_or_deleted_ids.add(cid)
            else:
                valid_chunk_ids.add(cid)
                if doc_id:
                    valid_doc_ids.add(doc_id)

    return {
        "world_id": clean_world_id,
        "valid_entry_source_ids": frozenset(valid_entry_ids),
        "valid_chunk_source_ids": frozenset(valid_chunk_ids),
        "valid_source_ids": frozenset(valid_entry_ids | valid_chunk_ids),
        "valid_document_ids": frozenset(valid_doc_ids),
        "unconfirmed_or_deleted_source_ids": frozenset(unconfirmed_or_deleted_ids),
        "other_world_source_ids": other_world_sources,
    }


def detect_setting_conflicts(
    sources: Sequence[CanonExcerptItem],
) -> tuple[SettingConflictItem, ...]:
    """检测已确认设定条目与导入文档分块之间（或不同分块之间）的明显设定矛盾。"""
    conflicts: list[SettingConflictItem] = []
    seen_pairs: set[tuple[str, str, str]] = set()

    strict_keywords = ("宵禁", "严禁", "禁止", "封锁", "停航", "关闭", "垄断", "接管", "征用")
    open_keywords = ("不设宵禁", "从不宵禁", "自由通行", "全天开放", "自由买卖", "自治", "从未封锁", "正常通航")

    entity_pattern = re.compile(
        r"([\u4e00-\u9fa5]{2,5}(?:水驿|驿站|外院|内院|镇|城|关|港|渡|洲|塔|商会|学派|船帮|巡盐司|矿务司|掌柜|渡头|执事|讲席|统领|铁匠))"
    )

    by_entity: dict[str, list[CanonExcerptItem]] = {}
    for src in sources:
        entities = set(entity_pattern.findall(f"{src.title} {src.text}"))
        for ent in entities:
            by_entity.setdefault(ent, []).append(src)

    for entity, group in sorted(by_entity.items()):
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                src_a = group[i]
                src_b = group[j]
                if src_a.source_id == src_b.source_id:
                    continue

                text_a = f"{src_a.title}：{src_a.text}"
                text_b = f"{src_b.title}：{src_b.text}"

                a_strict = any(k in text_a for k in strict_keywords) and not any(k in text_a for k in open_keywords)
                b_open = any(k in text_b for k in open_keywords)
                b_strict = any(k in text_b for k in strict_keywords) and not any(k in text_b for k in open_keywords)
                a_open = any(k in text_a for k in open_keywords)

                explicit_conflict = (
                    ("冲突" in text_a or "矛盾" in text_a or "误传" in text_b or "说法不一" in text_b)
                    and entity in src_a.text
                    and entity in src_b.text
                )

                if (a_strict and b_open) or (b_strict and a_open) or explicit_conflict:
                    if src_b.kind == "entry" and src_a.kind != "entry":
                        primary, secondary = src_b, src_a
                        policy: Any = "prefer_confirmed_entry"
                    elif src_a.kind == "entry" and src_b.kind != "entry":
                        primary, secondary = src_a, src_b
                        policy = "prefer_confirmed_entry"
                    else:
                        primary, secondary = src_a, src_b
                        policy = "preserve_both_as_rumor"

                    pair_key = (entity, primary.source_id, secondary.source_id)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    cid_hash = hashlib.sha1(
                        f"{entity}:{primary.source_id}:{secondary.source_id}".encode("utf-8")
                    ).hexdigest()[:8]
                    auth_excerpt = safe_u16_truncate(primary.text, 90)
                    conf_excerpt = safe_u16_truncate(secondary.text, 90)
                    summary = (
                        f"关于「{entity}」存在设定差异：{primary.source_id} 记载“{auth_excerpt}”，"
                        f"而 {secondary.source_id} 记载“{conf_excerpt}”。"
                        + (
                            "已优先采用已确认设定条目，并将异文保留为坊间传闻。"
                            if policy == "prefer_confirmed_entry"
                            else "已在草案中保留双方说法供开局核对或作为传闻线索。"
                        )
                    )
                    conflicts.append(
                        SettingConflictItem(
                            conflict_id=f"conf_{cid_hash}",
                            topic=f"{entity}设定差异",
                            entity_ref=entity,
                            summary=safe_u16_truncate(summary, 280),
                            primary_source_id=primary.source_id,
                            conflicting_source_id=secondary.source_id,
                            resolution_policy=policy,
                            authoritative_claim=auth_excerpt,
                            conflicting_claim=conf_excerpt,
                            player_visible_hint=True,
                        )
                    )

    return tuple(conflicts[:12])


def extract_and_batch_world_sources(
    conn: sqlite3.Connection,
    world_id: str,
    starting_point: StartingPointSpec | Mapping[str, Any] | str,
    *,
    selected_source_ids: Sequence[str] | None = None,
    selected_document_ids: Sequence[str] | None = None,
    selected_entry_ids: Sequence[str] | None = None,
    batch_max_entries: int = DEFAULT_BATCH_MAX_ENTRIES,
    batch_max_chunks: int = DEFAULT_BATCH_MAX_CHUNKS,
    max_entry_chars: int = DEFAULT_MAX_ENTRY_CHARS,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    batch_char_budget: int = DEFAULT_BATCH_CHAR_BUDGET,
    total_char_budget: int = DEFAULT_TOTAL_CHAR_BUDGET,
    max_batches: int = DEFAULT_MAX_BATCHES,
    raise_on_empty: bool = False,
) -> dict[str, Any]:
    """从数据库读取当前世界的已确认设定和导入资料，按起点挑选相关节选并生成长文分批计划。"""
    sp = normalize_starting_point(starting_point)
    ownership = discover_world_source_ownership(conn, world_id)
    clean_world_id = ownership["world_id"]
    other_world_map: dict[str, str] = ownership["other_world_source_ids"]
    valid_sources: frozenset[str] = ownership["valid_source_ids"]
    unconfirmed_or_deleted: frozenset[str] = ownership["unconfirmed_or_deleted_source_ids"]

    explicit_filter_ids: set[str] = set()
    if selected_source_ids is not None:
        for raw_sid in selected_source_ids:
            sid = str(raw_sid or "").strip()
            if not sid:
                continue
            if sid in other_world_map:
                raise CrossWorldSourceError(clean_world_id, sid, other_world_map[sid])
            if sid in unconfirmed_or_deleted:
                raise SourceFabricatedError(sid, f"来源 {sid!r} 尚未确认或已被删除，不能用于开局分析。")
            if sid not in valid_sources:
                raise SourceFabricatedError(sid, f"来源 {sid!r} 在当前世界 ({clean_world_id}) 中不存在。")
            explicit_filter_ids.add(sid)

    if selected_entry_ids is not None:
        for raw_eid in selected_entry_ids:
            eid_str = str(raw_eid or "").strip()
            if not eid_str:
                continue
            sid = eid_str if eid_str.startswith("entry:") else f"entry:{eid_str}"
            if sid in other_world_map:
                raise CrossWorldSourceError(clean_world_id, sid, other_world_map[sid])
            if sid in unconfirmed_or_deleted:
                raise SourceFabricatedError(sid, f"设定条目 {sid!r} 尚未确认或已被删除。")
            if sid not in valid_sources:
                raise SourceFabricatedError(sid, f"设定条目 {sid!r} 在当前世界中不存在。")
            explicit_filter_ids.add(sid)

    explicit_doc_filter: set[str] = set()
    if selected_document_ids is not None:
        for raw_did in selected_document_ids:
            did = str(raw_did or "").strip()
            if not did:
                continue
            if f"doc:{did}" in other_world_map:
                raise CrossWorldSourceError(clean_world_id, f"doc:{did}", other_world_map[f"doc:{did}"])
            if did not in ownership["valid_document_ids"]:
                raise SourceFabricatedError(f"doc:{did}", f"文档 {did!r} 在当前世界中不存在或已被删除。")
            explicit_doc_filter.add(did)

    has_explicit_filter = (
        selected_source_ids is not None
        or selected_entry_ids is not None
        or selected_document_ids is not None
    )

    search_tokens = _extract_search_tokens(sp)
    tables = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    entry_items: list[CanonExcerptItem] = []
    chunk_items: list[CanonExcerptItem] = []

    if "lore_entries" in tables:
        entry_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(lore_entries)").fetchall()}
        status_sql = "AND status = 'confirmed'" if "status" in entry_cols else ""
        del_sql = "AND deleted_at IS NULL" if "deleted_at" in entry_cols else ""
        rows = conn.execute(
            f"""
            SELECT id, type, title, summary
            FROM lore_entries
            WHERE world_id = ? {del_sql} {status_sql}
            ORDER BY id ASC
            """,
            (clean_world_id,),
        ).fetchall()
        for r in rows:
            raw_id = str(r[0] or "").strip()
            sid = f"entry:{raw_id}"
            if has_explicit_filter and sid not in explicit_filter_ids:
                continue
            etype = str(r[1] or "other").strip()
            etitle = str(r[2] or "").strip()
            esummary = str(r[3] or "").strip()
            raw_body = f"【{etitle}】{esummary}" if (etitle and esummary and etitle not in esummary) else (esummary or etitle)
            clean_body, inj_filtered = sanitize_untrusted_source_text(raw_body)
            if not raw_id or not clean_body:
                continue
            raw_units = u16_units(clean_body, "entry.text")
            if raw_units > max_entry_chars:
                excerpt_text = safe_u16_truncate(clean_body, max_entry_chars).strip()
                truncated = True
            else:
                excerpt_text = clean_body
                truncated = False
            score, matched = _score_source_relevance(
                title=etitle,
                heading_path=etype,
                text=excerpt_text,
                kind="entry",
                entry_type=etype,
                chunk_index=None,
                search_tokens=search_tokens,
            )
            entry_items.append(
                CanonExcerptItem(
                    source_id=sid,
                    world_id=clean_world_id,
                    kind="entry",
                    title=etitle or f"设定条目 {raw_id}",
                    text=excerpt_text,
                    entry_type=etype,
                    start_char=0,
                    end_char=len(excerpt_text),
                    relevance_score=score,
                    matched_terms=matched,
                    char_count=u16_units(excerpt_text, "entry.excerpt"),
                    truncated=truncated,
                    prompt_injection_filtered=inj_filtered,
                )
            )

    if "chunks" in tables and "documents" in tables:
        doc_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        status_sql = "AND (d.status IS NULL OR d.status = 'ready')" if "status" in doc_cols else ""
        del_sql = "AND d.deleted_at IS NULL" if "deleted_at" in doc_cols else ""
        rows = conn.execute(
            f"""
            SELECT c.id, c.document_id, c.chunk_index, c.heading_path, c.start_char, c.end_char, c.text, d.filename
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.world_id = ? {del_sql} {status_sql}
            ORDER BY d.id ASC, c.chunk_index ASC, c.id ASC
            """,
            (clean_world_id,),
        ).fetchall()
        for r in rows:
            cid = str(r[0] or "").strip()
            sid = f"chunk:{cid}"
            doc_id = str(r[1] or "").strip()
            if has_explicit_filter and sid not in explicit_filter_ids and doc_id not in explicit_doc_filter:
                continue
            c_idx = int(r[2] or 0)
            heading = str(r[3] or "").strip()
            start_c = int(r[4] or 0)
            end_c = int(r[5] or 0)
            raw_ctext = str(r[6] or "").strip()
            fname = str(r[7] or doc_id).strip()
            clean_ctext, inj_filtered = sanitize_untrusted_source_text(raw_ctext)
            if not cid or not clean_ctext:
                continue
            raw_units = u16_units(clean_ctext, "chunk.text")
            if raw_units > max_chunk_chars:
                excerpt_text = safe_u16_truncate(clean_ctext, max_chunk_chars).strip()
                truncated = True
            else:
                excerpt_text = clean_ctext
                truncated = False
            title_label = f"{fname} · {heading}" if (fname and heading) else (heading or fname or f"分块 {cid}")
            score, matched = _score_source_relevance(
                title=title_label,
                heading_path=heading,
                text=excerpt_text,
                kind="chunk",
                entry_type="",
                chunk_index=c_idx,
                search_tokens=search_tokens,
            )
            chunk_items.append(
                CanonExcerptItem(
                    source_id=sid,
                    world_id=clean_world_id,
                    kind="chunk",
                    title=title_label,
                    text=excerpt_text,
                    document_id=doc_id,
                    filename=fname,
                    chunk_index=c_idx,
                    start_char=start_c,
                    end_char=end_c if end_c > start_c else start_c + len(raw_ctext),
                    heading_path=heading,
                    relevance_score=score,
                    matched_terms=matched,
                    char_count=u16_units(excerpt_text, "chunk.excerpt"),
                    truncated=truncated,
                    prompt_injection_filtered=inj_filtered,
                )
            )

    if not entry_items and not chunk_items:
        if raise_on_empty:
            raise AnalysisSourceRequiredError(clean_world_id)
        return {
            "world_id": clean_world_id,
            "starting_point": sp.to_dict(),
            "status": "insufficient_sources",
            "blocked": True,
            "reason_code": "ANALYSIS_SOURCE_REQUIRED",
            "reason_message": "当前世界没有可用的已确认设定条目或就绪导入文档，请先导入资料或确认至少一条世界设定。",
            "batches": [],
            "input_ranges": [],
            "selected_sources": [],
            "allowed_source_ids": [],
            "confirmed_entry_source_ids": [],
            "other_world_source_ids": other_world_map,
            "detected_conflicts": [],
            "budget": {
                "batch_char_budget": batch_char_budget,
                "total_char_budget": total_char_budget,
                "max_batches": max_batches,
                "total_batches": 0,
                "used_chars": 0,
            },
        }

    entry_items.sort(
        key=lambda item: (
            -item.relevance_score,
            ANALYSIS_ENTRY_TYPE_PRIORITY.get(item.entry_type.lower(), 99),
            item.source_id,
        )
    )
    chunk_items.sort(
        key=lambda item: (
            -item.relevance_score,
            item.chunk_index if item.chunk_index is not None else 9999,
            item.document_id,
            item.source_id,
        )
    )

    raw_batches: list[list[CanonExcerptItem]] = []
    remaining_entries = list(entry_items)
    remaining_chunks = list(chunk_items)
    total_used_chars = 0

    while (remaining_entries or remaining_chunks) and len(raw_batches) < max_batches:
        if total_used_chars >= total_char_budget:
            break
        batch_sources: list[CanonExcerptItem] = []
        batch_chars = 0
        batch_entry_count = 0
        batch_chunk_count = 0

        if len(raw_batches) >= 1 and remaining_chunks:
            remaining_chunks.sort(
                key=lambda item: (
                    item.document_id,
                    item.chunk_index if item.chunk_index is not None else 9999,
                    -item.relevance_score,
                    item.source_id,
                )
            )

        progressed = True
        while progressed and (remaining_entries or remaining_chunks):
            progressed = False
            if remaining_entries and batch_entry_count < batch_max_entries:
                cand = remaining_entries[0]
                if (
                    batch_chars + cand.char_count <= batch_char_budget
                    and total_used_chars + cand.char_count <= total_char_budget
                ):
                    batch_sources.append(remaining_entries.pop(0))
                    batch_chars += cand.char_count
                    total_used_chars += cand.char_count
                    batch_entry_count += 1
                    progressed = True
                elif not batch_sources:
                    trimmed_text = safe_u16_truncate(cand.text, min(batch_char_budget, total_char_budget - total_used_chars))
                    trimmed_item = CanonExcerptItem(
                        **{
                            **cand.to_dict(),
                            "matched_terms": cand.matched_terms,
                            "text": trimmed_text,
                            "char_count": u16_units(trimmed_text),
                            "truncated": True,
                        }
                    )
                    remaining_entries.pop(0)
                    batch_sources.append(trimmed_item)
                    batch_chars += trimmed_item.char_count
                    total_used_chars += trimmed_item.char_count
                    batch_entry_count += 1
                    progressed = True

            if remaining_chunks and batch_chunk_count < batch_max_chunks:
                cand_c = remaining_chunks[0]
                if (
                    batch_chars + cand_c.char_count <= batch_char_budget
                    and total_used_chars + cand_c.char_count <= total_char_budget
                ):
                    batch_sources.append(remaining_chunks.pop(0))
                    batch_chars += cand_c.char_count
                    total_used_chars += cand_c.char_count
                    batch_chunk_count += 1
                    progressed = True
                elif not batch_sources:
                    trimmed_text = safe_u16_truncate(cand_c.text, min(batch_char_budget, total_char_budget - total_used_chars))
                    trimmed_item = CanonExcerptItem(
                        **{
                            **cand_c.to_dict(),
                            "matched_terms": cand_c.matched_terms,
                            "text": trimmed_text,
                            "char_count": u16_units(trimmed_text),
                            "truncated": True,
                        }
                    )
                    remaining_chunks.pop(0)
                    batch_sources.append(trimmed_item)
                    batch_chars += trimmed_item.char_count
                    total_used_chars += trimmed_item.char_count
                    batch_chunk_count += 1
                    progressed = True

        if not batch_sources:
            break
        raw_batches.append(batch_sources)

    total_batches = len(raw_batches)
    extraction_batches: list[ExtractionBatch] = []
    input_ranges: list[BatchInputRange] = []
    all_selected_sources: list[CanonExcerptItem] = []

    for b_idx, b_items in enumerate(raw_batches):
        all_selected_sources.extend(b_items)
        s_ids = tuple(item.source_id for item in b_items)
        e_ids = tuple(item.source_id for item in b_items if item.kind == "entry")
        c_ids = tuple(item.source_id for item in b_items if item.kind == "chunk")

        by_doc: dict[str, list[CanonExcerptItem]] = {}
        for item in b_items:
            if item.kind == "chunk" and item.document_id:
                by_doc.setdefault(item.document_id, []).append(item)

        doc_ranges: list[DocumentSpanRange] = []
        for doc_id, doc_chunks in sorted(by_doc.items()):
            doc_chunks.sort(key=lambda x: (x.chunk_index or 0, x.source_id))
            doc_ranges.append(
                DocumentSpanRange(
                    document_id=doc_id,
                    filename=doc_chunks[0].filename or doc_id,
                    start_chunk_index=min(c.chunk_index or 0 for c in doc_chunks),
                    end_chunk_index=max(c.chunk_index or 0 for c in doc_chunks),
                    start_char=min(c.start_char for c in doc_chunks),
                    end_char=max(c.end_char for c in doc_chunks),
                    chunk_ids=tuple(c.source_id for c in doc_chunks),
                )
            )

        used_c = sum(item.char_count for item in b_items)
        reason = (
            f"第 1/{total_batches} 批：按起点「{sp.location}」相关性优先抽取的已确认设定与核心章节分块"
            if b_idx == 0
            else f"第 {b_idx + 1}/{total_batches} 批：长文分批顺序抽取的后续章节分块与补充设定"
        )
        inp_range = BatchInputRange(
            batch_index=b_idx,
            total_batches=total_batches,
            source_ids=s_ids,
            entry_ids=e_ids,
            chunk_ids=c_ids,
            document_ranges=tuple(doc_ranges),
            used_chars=used_c,
            char_budget=batch_char_budget,
            selection_reason=reason,
        )
        input_ranges.append(inp_range)
        extraction_batches.append(
            ExtractionBatch(
                batch_index=b_idx,
                total_batches=total_batches,
                world_id=clean_world_id,
                starting_point=sp,
                sources=tuple(b_items),
                input_range=inp_range,
            )
        )

    conflicts = detect_setting_conflicts(all_selected_sources)
    confirmed_entry_ids = sorted(
        item.source_id for item in all_selected_sources if item.kind == "entry"
    )
    selected_source_id_list = [item.source_id for item in all_selected_sources]

    return {
        "world_id": clean_world_id,
        "starting_point": sp.to_dict(),
        "status": "ready",
        "blocked": False,
        "reason_code": "OK",
        "reason_message": f"已按起点「{sp.location}」完成资料挑选，共 {total_batches} 批（{len(all_selected_sources)} 条节选）。",
        "batches": extraction_batches,
        "batches_dict": [b.to_dict() for b in extraction_batches],
        "input_ranges": input_ranges,
        "input_ranges_dict": [r.to_dict() for r in input_ranges],
        "selected_sources": all_selected_sources,
        "selected_sources_dict": [s.to_dict() for s in all_selected_sources],
        "allowed_source_ids": selected_source_id_list,
        "all_world_valid_source_ids": sorted(valid_sources),
        "confirmed_entry_source_ids": confirmed_entry_ids,
        "other_world_source_ids": other_world_map,
        "detected_conflicts": conflicts,
        "detected_conflicts_dict": [c.to_dict() for c in conflicts],
        "budget": {
            "batch_max_entries": batch_max_entries,
            "batch_max_chunks": batch_max_chunks,
            "max_entry_chars": max_entry_chars,
            "max_chunk_chars": max_chunk_chars,
            "batch_char_budget": batch_char_budget,
            "total_char_budget": total_char_budget,
            "max_batches": max_batches,
            "total_batches": total_batches,
            "used_chars": total_used_chars,
            "total_available_entries": len(entry_items),
            "total_available_chunks": len(chunk_items),
            "remaining_unbatched_entries": len(remaining_entries),
            "remaining_unbatched_chunks": len(remaining_chunks),
        },
    }


def _slugify_identifier(prefix: str, text: str, fallback_idx: int) -> str:
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    if ascii_part and len(ascii_part) >= 3:
        return f"{prefix}_{ascii_part[:32]}"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{digest}_{fallback_idx}"


def merge_batch_extractions(
    batch_partials: Sequence[Mapping[str, Any]],
    batches: Sequence[ExtractionBatch],
    *,
    world_id: str,
    instance_id: str,
    starting_point: StartingPointSpec,
    detected_conflicts: Sequence[SettingConflictItem] = (),
) -> dict[str, Any]:
    """将多批长文抽取的模型阶段性输出（`batch_partials`）合并为统一的初始局势草案字典。"""
    if len(batch_partials) != len(batches):
        raise AnalysisValidationError(
            "BATCH_COUNT_MISMATCH",
            f"分批抽取结果数量 ({len(batch_partials)}) 与分批计划数量 ({len(batches)}) 不一致。",
        )

    merged_locations: dict[str, dict[str, Any]] = {}
    merged_npcs: list[dict[str, Any]] = []
    merged_events: dict[str, dict[str, Any]] = {}
    event_id_remap: dict[str, str] = {}
    npc_id_remap: dict[str, str] = {}
    merged_relationships: list[dict[str, Any]] = []
    merged_conflicts: dict[str, dict[str, Any]] = {
        c.conflict_id: c.to_dict() for c in detected_conflicts
    }
    identity_resolution_log: list[dict[str, Any]] = []
    scene_overviews: list[str] = []
    summaries: list[str] = []

    for b_idx, (partial, batch_meta) in enumerate(zip(batch_partials, batches)):
        if not isinstance(partial, Mapping):
            raise AnalysisValidationError("BATCH_OUTPUT_INVALID", f"第 {b_idx} 批输出必须为 JSON 对象。")
        allowed_in_batch = frozenset(batch_meta.input_range.source_ids)

        if isinstance(partial.get("scene_overview"), str) and partial["scene_overview"].strip():
            scene_overviews.append(partial["scene_overview"].strip())
        if isinstance(partial.get("summary"), str) and partial["summary"].strip():
            summaries.append(partial["summary"].strip())

        # 1. 合并地点
        for loc_idx, raw_loc in enumerate(partial.get("locations") or ()):
            if not isinstance(raw_loc, Mapping):
                continue
            loc_name = str(raw_loc.get("name") or "").strip()
            if not loc_name:
                continue
            loc_sources = [str(s).strip() for s in (raw_loc.get("source_ids") or ()) if str(s).strip()]
            for sid in loc_sources:
                if sid not in allowed_in_batch:
                    raise SourceFabricatedError(
                        sid,
                        f"第 {b_idx} 批地点「{loc_name}」引用了不在该批输入范围 {list(allowed_in_batch)} 内的来源 {sid!r}。",
                    )
            if loc_name not in merged_locations:
                merged_locations[loc_name] = {
                    **dict(raw_loc),
                    "location_id": str(raw_loc.get("location_id") or _slugify_identifier("loc", loc_name, len(merged_locations) + 1)),
                    "name": loc_name,
                    "source_ids": list(dict.fromkeys(loc_sources)),
                }
            else:
                existing_loc = merged_locations[loc_name]
                combined_sids = list(dict.fromkeys([*existing_loc.get("source_ids", ()), *loc_sources]))
                existing_loc["source_ids"] = combined_sids
                if raw_loc.get("grounding_type") == "canon":
                    existing_loc["grounding_type"] = "canon"

        # 2. 合并初始事件线
        for ev_idx, raw_ev in enumerate(partial.get("events") or ()):
            if not isinstance(raw_ev, Mapping):
                continue
            ev_title = str(raw_ev.get("title") or "").strip()
            if not ev_title:
                continue
            ev_sources = [str(s).strip() for s in (raw_ev.get("source_ids") or ()) if str(s).strip()]
            for sid in ev_sources:
                if sid not in allowed_in_batch:
                    raise SourceFabricatedError(
                        sid,
                        f"第 {b_idx} 批事件「{ev_title}」引用了不在该批输入范围内的来源 {sid!r}。",
                    )
            raw_ev_id = str(raw_ev.get("event_id") or _slugify_identifier("evt", ev_title, len(merged_events) + 1))
            if ev_title not in merged_events:
                merged_events[ev_title] = {
                    **dict(raw_ev),
                    "event_id": raw_ev_id,
                    "title": ev_title,
                    "source_ids": list(dict.fromkeys(ev_sources)),
                }
                event_id_remap[raw_ev_id] = raw_ev_id
            else:
                existing_ev = merged_events[ev_title]
                event_id_remap[raw_ev_id] = existing_ev["event_id"]
                existing_ev["source_ids"] = list(
                    dict.fromkeys([*(existing_ev.get("source_ids") or ()), *ev_sources])
                )
                existing_parts = list(existing_ev.get("participants") or ())
                for p in raw_ev.get("participants") or ():
                    if str(p).strip() and str(p).strip() not in existing_parts:
                        existing_parts.append(str(p).strip())
                existing_ev["participants"] = existing_parts
                if raw_ev.get("grounding_type") == "canon":
                    existing_ev["grounding_type"] = "canon"

        # 3. 合并人物
        for npc_idx, raw_npc in enumerate(partial.get("npcs") or ()):
            if not isinstance(raw_npc, Mapping):
                continue
            npc_name = str(raw_npc.get("name") or "").strip()
            if not npc_name:
                continue
            npc_sources = [str(s).strip() for s in (raw_npc.get("source_ids") or ()) if str(s).strip()]
            for sid in npc_sources:
                if sid not in allowed_in_batch:
                    raise SourceFabricatedError(
                        sid,
                        f"第 {b_idx} 批人物「{npc_name}」引用了不在该批输入范围内的来源 {sid!r}。",
                    )
            incoming_id = str(raw_npc.get("npc_id") or "").strip()
            incoming_role = str(raw_npc.get("role") or raw_npc.get("identity") or "").strip()
            incoming_home = str(raw_npc.get("home_location") or raw_npc.get("location") or "").strip()
            incoming_aliases = [
                str(a).strip() for a in (raw_npc.get("aliases") or ()) if str(a).strip()
            ]
            incoming_disamb = str(raw_npc.get("homonym_disambiguator") or "").strip()

            matched_existing: dict[str, Any] | None = None
            homonym_existing: dict[str, Any] | None = None

            for cand in merged_npcs:
                cand_name = str(cand.get("name") or "").strip()
                cand_role = str(cand.get("role") or "").strip()
                cand_home = str(cand.get("home_location") or cand.get("location") or "").strip()
                cand_aliases = set(cand.get("aliases") or ())

                same_name_or_alias = (
                    npc_name == cand_name
                    or npc_name in cand_aliases
                    or cand_name in incoming_aliases
                    or bool(set(incoming_aliases) & cand_aliases)
                )
                if same_name_or_alias:
                    distinct_locations = bool(incoming_home and cand_home and incoming_home != cand_home)
                    distinct_roles = bool(incoming_role and cand_role and incoming_role != cand_role)
                    explicit_different_disamb = bool(
                        incoming_disamb
                        and cand.get("homonym_disambiguator")
                        and incoming_disamb != cand.get("homonym_disambiguator")
                    )
                    if npc_name == cand_name and (
                        explicit_different_disamb or (distinct_locations and distinct_roles)
                    ):
                        homonym_existing = cand
                        continue
                    matched_existing = cand
                    break

            if matched_existing is not None:
                prev_id = str(matched_existing["npc_id"])
                if incoming_id:
                    npc_id_remap[incoming_id] = prev_id
                all_aliases = list(
                    dict.fromkeys(
                        [
                            *(matched_existing.get("aliases") or ()),
                            *incoming_aliases,
                            *(
                                [npc_name]
                                if npc_name != matched_existing["name"]
                                else []
                            ),
                        ]
                    )
                )
                matched_existing["aliases"] = all_aliases
                matched_existing["source_ids"] = list(
                    dict.fromkeys([*(matched_existing.get("source_ids") or ()), *npc_sources])
                )
                if raw_npc.get("grounding_type") == "canon":
                    matched_existing["grounding_type"] = "canon"
                if raw_npc.get("goal") and not matched_existing.get("goal"):
                    matched_existing["goal"] = raw_npc["goal"]
                if raw_npc.get("relationships"):
                    matched_existing["relationships"] = list(matched_existing.get("relationships") or []) + list(
                        raw_npc["relationships"]
                    )
                identity_resolution_log.append(
                    {
                        "action": "merged_alias",
                        "canonical_npc_id": prev_id,
                        "name": matched_existing["name"],
                        "merged_name_or_alias": npc_name,
                        "aliases": all_aliases,
                        "batch_index": b_idx,
                        "reason": f"将第 {b_idx + 1} 批的「{npc_name}」与既有「{matched_existing['name']}」合并为同一实例人物 ({prev_id})。",
                    }
                )
            else:
                new_npc = dict(raw_npc)
                new_npc["name"] = npc_name
                new_npc["aliases"] = incoming_aliases
                new_npc["source_ids"] = list(dict.fromkeys(npc_sources))

                if homonym_existing is not None:
                    if not homonym_existing.get("homonym_disambiguator"):
                        h_loc = homonym_existing.get("home_location") or homonym_existing.get("location") or "甲处"
                        h_role = homonym_existing.get("role") or "人物"
                        homonym_existing["homonym_disambiguator"] = f"{h_loc}·{h_role}"
                    disamb_label = incoming_disamb or f"{incoming_home or '乙处'}·{incoming_role or '人物'}"
                    new_npc["homonym_disambiguator"] = disamb_label
                    base_id = incoming_id or _slugify_identifier("npc", f"{npc_name}_{disamb_label}", len(merged_npcs) + 1)
                    existing_ids = {str(n.get("npc_id")) for n in merged_npcs}
                    if base_id in existing_ids:
                        base_id = f"{base_id}_{len(merged_npcs) + 1}"
                    new_npc["npc_id"] = base_id
                    if incoming_id:
                        npc_id_remap.setdefault(incoming_id, base_id)
                    identity_resolution_log.append(
                        {
                            "action": "disambiguated_homonym",
                            "name": npc_name,
                            "first_npc_id": homonym_existing["npc_id"],
                            "first_disambiguator": homonym_existing["homonym_disambiguator"],
                            "second_npc_id": base_id,
                            "second_disambiguator": disamb_label,
                            "batch_index": b_idx,
                            "reason": (
                                f"检测到重名人物「{npc_name}」分别属于「{homonym_existing['homonym_disambiguator']}」"
                                f"与「{disamb_label}」，已拆分为独立实例身份 {homonym_existing['npc_id']} 与 {base_id}。"
                            ),
                        }
                    )
                else:
                    base_id = incoming_id or _slugify_identifier("npc", npc_name, len(merged_npcs) + 1)
                    existing_ids = {str(n.get("npc_id")) for n in merged_npcs}
                    if base_id in existing_ids:
                        base_id = f"{base_id}_{len(merged_npcs) + 1}"
                    new_npc["npc_id"] = base_id
                    if incoming_id:
                        npc_id_remap.setdefault(incoming_id, base_id)

                merged_npcs.append(new_npc)

        # 4. 合并顶层人物关系
        for raw_rel in partial.get("relationships") or ():
            if isinstance(raw_rel, Mapping):
                rel_sources = [str(s).strip() for s in (raw_rel.get("source_ids") or ()) if str(s).strip()]
                for sid in rel_sources:
                    if sid not in allowed_in_batch:
                        raise SourceFabricatedError(
                            sid,
                            f"第 {b_idx} 批人物关系引用了不在该批输入范围内的来源 {sid!r}。",
                        )
                merged_relationships.append(dict(raw_rel))

        # 5. 合并批次内报告的设定冲突
        for raw_conf in partial.get("conflicts") or ():
            if isinstance(raw_conf, Mapping) and raw_conf.get("conflict_id"):
                cid = str(raw_conf["conflict_id"])
                merged_conflicts.setdefault(cid, dict(raw_conf))

    all_events_list = list(merged_events.values())
    events_list = all_events_list[:6]
    retained_event_ids = {str(ev["event_id"]) for ev in events_list}
    fallback_event_id = str(events_list[0]["event_id"]) if events_list else ""

    merged_npcs = merged_npcs[:12]
    retained_npc_ids = {str(n["npc_id"]) for n in merged_npcs}
    alias_to_npc_id: dict[str, str] = dict(npc_id_remap)
    for n in merged_npcs:
        nid = str(n["npc_id"])
        alias_to_npc_id[nid] = nid
        if not n.get("homonym_disambiguator"):
            alias_to_npc_id[str(n["name"])] = nid
        for al in n.get("aliases") or ():
            alias_to_npc_id.setdefault(str(al), nid)

    for n in merged_npcs:
        t_id = str(n.get("target_npc_id") or "").strip()
        if t_id and t_id in alias_to_npc_id:
            n["target_npc_id"] = alias_to_npc_id[t_id]

        if len(batches) > 1 and retained_event_ids:
            remapped_causes: list[str] = []
            for cid in n.get("cause_event_ids") or ():
                mapped = event_id_remap.get(str(cid), str(cid))
                target_ev = mapped if mapped in retained_event_ids else fallback_event_id
                if target_ev and target_ev not in remapped_causes:
                    remapped_causes.append(target_ev)
            n["cause_event_ids"] = remapped_causes

            remapped_known: list[str] = []
            for kid in n.get("initial_known_event_ids") or ():
                mapped = event_id_remap.get(str(kid), str(kid))
                target_ev = mapped if mapped in retained_event_ids else fallback_event_id
                if target_ev and target_ev not in remapped_known:
                    remapped_known.append(target_ev)
            n["initial_known_event_ids"] = remapped_known

    dedup_rels: list[dict[str, Any]] = []
    seen_rel_keys: set[tuple[str, str, str]] = set()
    for rel in merged_relationships:
        raw_f = str(rel.get("from_npc_id") or "").strip()
        raw_t = str(rel.get("to_target_id") or "").strip()
        f_id = alias_to_npc_id.get(raw_f, raw_f)
        t_id = alias_to_npc_id.get(raw_t, raw_t)
        rtype = str(rel.get("relation_type") or "acquaintance").strip()
        rkey = (f_id, t_id, rtype)
        if (
            f_id
            and t_id
            and f_id != t_id
            and f_id in retained_npc_ids
            and rkey not in seen_rel_keys
        ):
            seen_rel_keys.add(rkey)
            dedup_rels.append({**rel, "from_npc_id": f_id, "to_target_id": t_id, "relation_type": rtype})

    return {
        "world_id": world_id,
        "instance_id": instance_id,
        "starting_point": starting_point.to_dict(),
        "world_minute": starting_point.world_minute,
        "summary": summaries[0] if summaries else f"已完成「{starting_point.location}」多批设定合并与初始局势构建。",
        "scene_overview": scene_overviews[0] if scene_overviews else f"你抵达了{starting_point.location}，周围局势正在悄然运转。",
        "locations": list(merged_locations.values())[:16],
        "npcs": merged_npcs,
        "events": events_list,
        "relationships": dedup_rels,
        "conflicts": list(merged_conflicts.values()),
        "identity_resolution_log": identity_resolution_log,
    }
