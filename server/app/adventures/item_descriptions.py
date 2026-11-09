"""Player-written item lore is shared by adventures in the same world."""
from .state import StateError, item_name
from ..errors import ApiError

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_descriptions (
    world_id TEXT NOT NULL REFERENCES worlds(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (world_id, name)
);
"""
MAX_DESCRIPTION_UNITS = 2000
PROMPT_BUDGET = 6000


def for_character(conn, world_id, character):
    names = [item["name"] for item in (character or {}).get("inventory", [])]
    if not names:
        return {}
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT name, description FROM item_descriptions WHERE world_id=? AND name IN ({placeholders})",
        (world_id, *names),
    )
    return {row["name"]: row["description"] for row in rows}


def write(conn, run, payload):
    try:
        name = item_name(payload.get("name"))
    except StateError as exc:
        raise ApiError(422, "ITEM_INVALID", str(exc)) from None
    if not any(item["name"] == name for item in (run["character"] or {}).get("inventory", [])):
        raise ApiError(404, "ITEM_NOT_CARRIED", "当前背包里没有这件物品。")
    description = payload.get("description")
    if not isinstance(description, str):
        raise ApiError(422, "ITEM_DESCRIPTION_INVALID", "物品介绍必须为文字。")
    description = description.strip()
    try:
        units = len(description.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        raise ApiError(422, "ITEM_DESCRIPTION_INVALID", "物品介绍包含不完整字符。") from None
    if units > MAX_DESCRIPTION_UNITS:
        raise ApiError(422, "ITEM_DESCRIPTION_INVALID", "物品介绍最多 2000 字符。")
    if description:
        conn.execute(
            """INSERT INTO item_descriptions(world_id, name, description) VALUES (?, ?, ?)
               ON CONFLICT(world_id, name) DO UPDATE SET description=excluded.description""",
            (run["world_id"], name, description),
        )
    else:
        conn.execute(
            "DELETE FROM item_descriptions WHERE world_id=? AND name=?",
            (run["world_id"], name),
        )
    return {"name": name, "description": description}


def for_prompt(descriptions, action):
    ordered = sorted(descriptions.items(), key=lambda item: (item[0] not in action, item[0]))
    result = []
    remaining = PROMPT_BUDGET
    for name, description in ordered:
        cost = len((name + description).encode("utf-16-le")) // 2
        if cost <= remaining:
            result.append((name, description))
            remaining -= cost
    return tuple(result)
