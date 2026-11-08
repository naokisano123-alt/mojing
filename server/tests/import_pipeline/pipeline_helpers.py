"""测试辅助工具与 Fixtures（R5-L 独立导入回归套件）。

遵循 R5-L 规约：
- 独立于共享 conftest.py，不修改既有共享测试设置；
- 基于项目真实 create_app + TestClient + 临时 SQLite 数据库；
- 提供上下文管理器支持跨重启状态验证。
"""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@contextmanager
def temp_pipeline_client(
    overrides: dict[str, Any] | None = None,
    existing_db: Path | None = None,
) -> Generator[tuple[TestClient, Path], None, None]:
    """创建绑定隔离临时数据库的 TestClient。若传入 existing_db，则模拟服务重启。"""
    settings_kwargs = dict(overrides or {})

    if existing_db is not None:
        settings_kwargs["db_path"] = existing_db
        app = create_app(Settings(**settings_kwargs))
        with TestClient(app) as client:
            yield client, existing_db
    else:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "mojing-pipeline-test.db"
            settings_kwargs["db_path"] = db_path
            app = create_app(Settings(**settings_kwargs))
            with TestClient(app) as client:
                yield client, db_path


def create_test_world(
    client: TestClient,
    name: str = "云山纪",
    description: str = "一座悬浮于青冥之中的古老山脉。",
) -> dict[str, Any]:
    """在当前测试客户端中创建一个世界并返回其详情。"""
    res = client.post("/api/v1/worlds", json={"name": name, "description": description})
    assert res.status_code == 201, f"Failed to create world: {res.text}"
    return res.json()


def upload_document(
    client: TestClient,
    world_id: str,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    allow_duplicate: bool = False,
) -> Any:
    """向指定世界上传文档。"""
    files = {"file": (filename, data, content_type or "application/octet-stream")}
    params = {}
    if allow_duplicate:
        params["allow_duplicate"] = "true"
    return client.post(f"/api/v1/worlds/{world_id}/documents", files=files, params=params)


def fetch_raw_text(client: TestClient, document_id: str) -> str:
    """获取文档全文。"""
    res = client.get(f"/api/v1/documents/{document_id}/raw", params={"limit": 50000})
    assert res.status_code == 200, f"Failed to get raw text: {res.text}"
    return res.json()["text"]


def fetch_chunks(client: TestClient, document_id: str) -> list[dict[str, Any]]:
    """获取文档切片列表（包含全文）。"""
    res = client.get(
        f"/api/v1/documents/{document_id}/chunks",
        params={"include_text": "true", "limit": 200},
    )
    assert res.status_code == 200, f"Failed to get chunks: {res.text}"
    return res.json()["items"]
