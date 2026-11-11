"""Shared names for independent imported copies."""


def imported_world_name(conn, original: str) -> str:
    name, number = original.strip(), 1
    while conn.execute('SELECT 1 FROM worlds WHERE name=? AND deleted_at IS NULL', (name,)).fetchone():
        suffix = '（导入）' if number == 1 else f'（导入{number}）'
        name = original.strip()[:40 - len(suffix)] + suffix
        number += 1
    return name
