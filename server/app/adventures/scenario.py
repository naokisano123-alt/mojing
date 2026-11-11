"""Author-defined openings and branch-local world time."""
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import re
from uuid import uuid4

from ..errors import ApiError
from .state import StateError, character

SCHEMA = """
CREATE TABLE IF NOT EXISTS world_openings (
 id TEXT PRIMARY KEY,
 world_id TEXT NOT NULL REFERENCES worlds(id),
 opening_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_openings_world ON world_openings(world_id);
"""
MAX_ADVANCE = 7 * 1440
MAX_EVENTS = 20
MAX_OPENINGS = 20
CALENDAR_FORMAT = "%Y-%m-%dT%H:%M"
DURATION = re.compile(r"^(?:快进|接下来|未来)\s*"
                      r"([0-9]{1,4}|[一二两三四五六七八九十])\s*个?\s*(天|小时|分钟)")
NUMBERS = dict(zip("一二两三四五六七八九十", (1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10)))
ACTION_DURATION = re.compile(
    r"^(?:我)?(?:先|继续)?(?:等待|等候|等|休息|睡觉|睡|工作|做工|赶路|旅行|步行|冥想|停留)\s*"
    r"([0-9]{1,4}|[一二两三四五六七八九十半])\s*个?\s*(天|小时|分钟|夜)"
)


def _text(value, name, limit):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise StateError(f"{name}须为1–{limit}字。")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise StateError(f"{name}包含不完整字符。") from exc
    return value.strip()


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise StateError(f"{name}须为{low}–{high}的整数。")
    return value


def normalize(payload):
    if not isinstance(payload, dict):
        raise StateError("开局方案格式无效。")
    starts_at = _text(payload.get("starts_at"), "开局时间", 16)
    try:
        start = datetime.strptime(starts_at, CALENDAR_FORMAT)
    except ValueError:
        raise StateError("开局时间须为有效的世界日期和时刻。") from None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", starts_at) or start.year > 7999:
        raise StateError("开局年份须在0001至7999之间。")
    initial = asdict(character(payload.get("character")))
    initial["elapsed_minutes"] = 0
    events = payload.get("events", [])
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise StateError("每个开局最多20条日程。")
    normalized = []
    for event in events:
        if not isinstance(event, dict) or type(event.get("interrupt")) is not bool:
            raise StateError("日程事件格式无效。")
        every = event.get("every_minutes")
        normalized.append({
            "title": _text(event.get("title"), "事件名称", 80),
            "description": _text(event.get("description"), "发生后的描述", 500),
            "at_minute": _integer(event.get("at_minute"), "首次发生分钟", 1, 5_256_000),
            "every_minutes": _integer(every, "重复间隔", 60, 525_600) if every is not None else None,
            "interrupt": event["interrupt"],
        })
    shortcut = payload.get("shortcut", "")
    return {
        "name": _text(payload.get("name"), "开局名称", 80),
        "intro": _text(payload.get("intro"), "开场正文", 6000),
        "starts_at": starts_at,
        "shortcut": _text(shortcut, "预设行动", 300) if shortcut != "" else "",
        "character": initial,
        "events": normalized,
    }


def public(opening):
    return {key: opening[key] for key in ("name", "intro", "starts_at", "character", "shortcut")}


def _opening_rows(conn, world_id):
    return conn.execute(
        "SELECT id, opening_json FROM world_openings WHERE world_id=? ORDER BY rowid", (world_id,),
    )


def list_openings(conn, world_id, *, author=False):
    items = []
    for row in _opening_rows(conn, world_id):
        value = json.loads(row["opening_json"])
        items.append({"id": row["id"], **(value if author else public(value))})
    return items


def get_opening(conn, world_id, opening_id):
    row = conn.execute(
        "SELECT opening_json FROM world_openings WHERE world_id=? AND id=?", (world_id, opening_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "OPENING_NOT_FOUND", "开局方案不存在，请重新选择。")
    return json.loads(row["opening_json"])


def save_opening(conn, world_id, payload, opening_id=None):
    try:
        opening = normalize(payload)
    except StateError as exc:
        raise ApiError(422, "OPENING_INVALID", str(exc)) from None
    raw = json.dumps(opening, ensure_ascii=False)
    if opening_id is None:
        if len(list_openings(conn, world_id)) >= MAX_OPENINGS:
            raise ApiError(422, "OPENING_LIMIT", "每个世界最多20个开局方案。")
        opening_id = str(uuid4())
        conn.execute("INSERT INTO world_openings VALUES(?,?,?)", (opening_id, world_id, raw))
    else:
        get_opening(conn, world_id, opening_id)
        conn.execute("UPDATE world_openings SET opening_json=? WHERE id=?", (raw, opening_id))
    return {"id": opening_id, **opening}


def duration(action, requested):
    if requested is None:
        match = DURATION.match(action.strip()) or ACTION_DURATION.match(action.strip())
        if not match:
            return None
        quantity, unit = match.groups()
        count = 0.5 if quantity == '半' else NUMBERS.get(quantity, int(quantity) if quantity.isdigit() else 0)
        requested = int(count * {"天": 1440, "小时": 60, "分钟": 1, "夜": 480}[unit])
    try:
        return _integer(requested, "快进分钟数", 1, MAX_ADVANCE)
    except StateError as exc:
        raise ApiError(422, "CLOCK_DURATION_INVALID", str(exc)) from None


def world_time(opening, elapsed):
    start = datetime.strptime(opening["starts_at"], CALENDAR_FORMAT)
    return (start + timedelta(minutes=elapsed)).isoformat(timespec="minutes")


def _next(event, after):
    first = event["at_minute"]
    if first > after:
        return first
    every = event["every_minutes"]
    return first + ((after - first) // every + 1) * every if every else None


def plan(opening, after, minutes):
    if after + minutes > 1_000_000_000:
        raise ApiError(422, "CLOCK_LIMIT", "世界时间已达到上限。")
    wanted = after + minutes
    reached = wanted
    upcoming = []
    for event in opening["events"]:
        at = _next(event, after)
        if at is not None and at <= wanted:
            upcoming.append((at, event))
            if event["interrupt"]:
                reached = min(reached, at)
    events = []
    for at, event in sorted(upcoming, key=lambda value: value[0]):
        if at <= reached:
            every = event["every_minutes"]
            count = (reached - at) // every + 1 if every else 1
            events.append({
                "title": event["title"], "description": event["description"],
                "at": world_time(opening, at), "count": count, "interrupt": event["interrupt"],
            })
    return {
        "requested_minutes": minutes, "elapsed_minutes": reached - after,
        "from": world_time(opening, after), "to": world_time(opening, reached),
        "interrupted": any(event["interrupt"] for event in events), "events": events,
    }
