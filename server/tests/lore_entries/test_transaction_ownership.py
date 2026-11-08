import sqlite3

import pytest

from app.lore_entries.service import create_entry, update_entry, delete_entry


@pytest.mark.parametrize('operation', ['create', 'update', 'delete'])
def test_caller_can_rollback_service_without_preexisting_transaction(client, db_path, operation):
    world = client.post('/api/v1/worlds', json={'name': 'Transaction test'}).json()
    initial = client.post(f"/api/v1/worlds/{world['id']}/lore-entries",
                          json={'type': 'rule', 'title': 'Original'}).json()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        assert not conn.in_transaction
        if operation == 'create':
            create_entry(conn, world['id'], {'type': 'rule', 'title': 'New'})
        elif operation == 'update':
            update_entry(conn, initial['id'], {'title': 'Changed'}, 1)
        else:
            delete_entry(conn, initial['id'], 1)
        assert conn.in_transaction, 'Service must leave commit/rollback to caller'
        conn.rollback()
    finally:
        conn.close()

    with sqlite3.connect(db_path) as observer:
        rows = observer.execute('SELECT title, revision, deleted_at FROM lore_entries').fetchall()
        assert rows == [('Original', 1, None)]
