"""Branch-local continuity records and their immutable, replayable log."""
import json
from dataclasses import asdict
from uuid import UUID, uuid4

from .state import StateError, project

SCHEMA = """
CREATE TABLE IF NOT EXISTS adventure_memory_events (
 adventure_id TEXT NOT NULL REFERENCES adventures(id),
 sequence INTEGER NOT NULL,
 turn INTEGER NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('turn','correction')),
 change_json TEXT NOT NULL,
 PRIMARY KEY(adventure_id, sequence)
);
"""
KINDS = ("deposit", "commitment", "secret")
ACTIVE_LIMIT = 100
MAX_CHANGES = 8


def _text(value, label, limit, *, optional=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (not value.strip() and not optional):
        raise StateError(f"{label}须为{'至多' if optional else '1–'}{limit}字。")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise StateError(f"{label}含不完整字符。") from None
    return value.strip()


def _id(value):
    if not isinstance(value, str):
        raise StateError("日志标识无效。")
    try:
        valid = str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise StateError("日志标识无效。")
    return value


def _people(value):
    if not isinstance(value, list) or len(value) > 20:
        raise StateError("知情人须为至多20个人名。")
    return list(dict.fromkeys(_text(name, "知情人", 80) for name in value))


def make_item(value, item_id):
    if not isinstance(value, dict):
        raise StateError("日志条目格式无效。")
    kind = value.get("kind")
    if kind not in KINDS:
        raise StateError("日志类型无效。")
    item = {
        "id": _id(item_id), "kind": kind,
        "title": _text(value.get("title"), "标题", 100),
        "detail": _text(value.get("detail", ""), "说明", 500, optional=True),
        "status": value.get("status", "active"),
    }
    states = {"deposit": ("active", "refund", "forfeit"), "commitment": ("active", "done"), "secret": ("active",)}
    if item["status"] not in states[kind]:
        raise StateError("该日志类型不支持这一状态。")
    if kind != "secret":
        item["person"] = _text(value.get("person"), "关联人物", 80)
    if kind == "deposit":
        amount = value.get("amount")
        if type(amount) is not int or not 1 <= amount <= 1000:
            raise StateError("押金金额须为1–1000的整数。")
        item["amount"] = amount
    if kind == "secret":
        item["knowers"] = _people(value.get("knowers", []))
    return item


def validate_event(value):
    if not isinstance(value, dict) or set(value) != {"id", "item", "reason"}:
        raise StateError("日志事件格式无效。")
    item_id = _id(value["id"])
    item = make_item(value["item"], item_id) if value["item"] is not None else None
    if item != value["item"]:
        raise StateError("日志条目内容无效。")
    return {"id": item_id, "item": item, "reason": _text(value["reason"], "变更说明", 300, optional=True)}


def replay(conn, adventure_id):
    items, events = {}, []
    for row in conn.execute("""
        SELECT sequence, turn, origin, change_json FROM adventure_memory_events
        WHERE adventure_id=? ORDER BY sequence
    """, (adventure_id,)):
        change = json.loads(row["change_json"])
        if change["item"] is None:
            items.pop(change["id"], None)
        else:
            items[change["id"]] = {**change["item"], "turn": row["turn"]}
        events.append({"sequence": row["sequence"], "turn": row["turn"], "origin": row["origin"], **change})
    return {"cursor": events[-1]["sequence"] if events else 0, "items": items, "events": events}


def cursor(conn, adventure_id):
    return conn.execute(
        "SELECT COALESCE(MAX(sequence),0) FROM adventure_memory_events WHERE adventure_id=?",
        (adventure_id,),
    ).fetchone()[0]


def view(snapshot):
    return {
        "cursor": snapshot["cursor"],
        "items": list(snapshot["items"].values()),
        "events": snapshot["events"][-30:][::-1],
    }


def append(conn, adventure_id, turn, cursor, origin, changes):
    conn.executemany("""
        INSERT INTO adventure_memory_events(adventure_id,sequence,turn,origin,change_json)
        VALUES(?,?,?,?,?)
    """, [(adventure_id, cursor + index, turn, origin, json.dumps(change, ensure_ascii=False))
          for index, change in enumerate(changes, 1)])


def correction(snapshot, payload):
    if not isinstance(payload, dict):
        raise StateError("日志更正格式无效。")
    if payload.get("id") is None:
        item_id = str(uuid4())
    else:
        item_id = _id(payload["id"])
        if item_id not in snapshot["items"]:
            raise StateError("待更正的条目不存在。")
    value = payload.get("item")
    if value is None and item_id not in snapshot["items"]:
        raise StateError("请选择要撤销的条目。")
    item = make_item(value, item_id) if value is not None else None
    existing = snapshot["items"].get(item_id)
    if item and item["status"] == "active" and (not existing or existing["status"] != "active"):
        if sum(entry["status"] == "active" for entry in snapshot["items"].values()) >= ACTIVE_LIMIT:
            raise StateError("本旅程同时生效的日志条目已达100条。")
    return {"id": item_id, "item": item, "reason": _text(payload.get("reason"), "更正说明", 300)}


def plan(items, operations, state, proposed, visible_ids):
    """Resolve model proposals against current facts and compute deposit cash movement."""
    if not isinstance(operations, (list, tuple)) or len(operations) > MAX_CHANGES:
        raise StateError("单回合最多提议8项日志变化。")
    current = {item["id"]: item for item in items}
    changes, touched = [], set()
    cash = 0
    settles_deposit = False
    for op in operations:
        if not isinstance(op, dict):
            raise StateError("日志提议格式无效。")
        action = op.get("op")
        item_id = op.get("id")
        if action == "record":
            item = make_item({**op, "status": "active"}, str(uuid4()))
            if sum(other["status"] == "active" for other in current.values()) >= ACTIVE_LIMIT or any(
                other["status"] == "active" and
                (other["kind"], other["title"], other.get("person")) == (item["kind"], item["title"], item.get("person"))
                for other in current.values()
            ):
                raise StateError("日志已达上限，或同名事项已存在。")
            if item["kind"] == "deposit":
                cash -= item["amount"]
                if state is None or state.coins + cash < 0:
                    raise StateError("角色余额不足，无法支付押金。")
            item_id = item["id"]
        else:
            item_id = _id(item_id)
            old = current.get(item_id)
            if not old or item_id not in visible_ids or old["status"] != "active" or item_id in touched:
                raise StateError("日志提议引用了无效、已结束或重复的事项。")
            if action == "resolve" and old["kind"] in ("deposit", "commitment"):
                outcome = op.get("outcome")
                allowed = ("refund", "forfeit") if old["kind"] == "deposit" else ("done",)
                if outcome not in allowed:
                    raise StateError("该事项不支持这一结算结果。")
                if outcome == "refund":
                    cash += old["amount"]
                item = make_item({**old, "status": outcome}, item_id)
            elif action == "learn" and old["kind"] == "secret":
                new_people = _people(op.get("knowers"))
                if not new_people or set(new_people) <= set(old["knowers"]):
                    raise StateError("请提供新增知情人。")
                item = make_item({**old, "knowers": list(dict.fromkeys(old["knowers"] + new_people))}, item_id)
            else:
                raise StateError("日志提议操作无效。")
        current[item_id] = item
        touched.add(item_id)
        settles_deposit |= item["kind"] == "deposit"
        reason = _text(op.get("reason", ""), "变更说明", 300, optional=action != "learn")
        changes.append({"id": item_id, "item": item, "reason": reason})
    effective = dict(proposed or {})
    coins_delta = effective.get("coins_delta", 0)
    if settles_deposit and (type(coins_delta) is not int or coins_delta != 0):
        raise StateError("押金由账本结算，本回合请勿另外提交钱币增减。")
    if cash:
        effective["coins_delta"] = effective.get("coins_delta", 0) + cash
    if state is None and (proposed is not None or cash):
        raise StateError("当前旅程没有角色账本。")
    projected = asdict(project(state, effective)) if state is not None and (proposed is not None or cash) else None
    return changes, projected


def context(conn, adventure_id, snapshot, action, location, recent, oldest_recent):
    """Include active facts and match older resolved events by title or participant."""
    recent_text = " ".join(recent)[-2400:].casefold()
    query = f"{action} {location}".casefold()

    def relevance(item):
        keys = (item["title"], item.get("person", ""), *item.get("knowers", []))
        return (any(key and key.casefold() in query for key in keys),
                any(key and key.casefold() in recent_text for key in keys))

    items = sorted(snapshot["items"].values(), key=lambda item: (
        *relevance(item), item["status"] == "active", item["kind"] != "secret", item["turn"],
    ), reverse=True)
    selected = []
    remaining = 8500
    events = {}
    for event in reversed(snapshot["events"]):
        history = events.setdefault(event["id"], [])
        if len(history) < 3:
            history.append({"turn": event["turn"], "status": (event["item"] or {}).get("status"),
                            "reason": event["reason"]})
    excerpts = {}
    for item in items:
        relevant = any(relevance(item))
        if item["status"] != "active" and not relevant:
            continue
        record = {**item, "events": events.get(item["id"], [])}
        # Keep each record intact so a truncated label cannot become a false fact.
        size = len(json.dumps(record, ensure_ascii=False).encode("utf-16-le")) // 2
        if size > remaining:
            continue
        selected.append(record)
        remaining -= size
        if relevant:
            for event in record["events"]:
                turn = event["turn"]
                if not 0 < turn < oldest_recent or turn in excerpts or len(excerpts) >= 3:
                    continue
                row = conn.execute("""
                    SELECT action, result FROM adventure_turns WHERE adventure_id=? AND sequence=?
                """, (adventure_id, turn)).fetchone()
                if row:
                    excerpts[turn] = {"turn": turn, "action": row["action"][:120],
                                      "narrative": json.loads(row["result"])["narrative"][:300]}
    return tuple(selected), tuple(excerpts.values())
