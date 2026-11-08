import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent
for _p in (REPO_ROOT, SERVER_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "mojing-test.db"


@pytest.fixture()
def client(db_path):
    app = create_app(Settings(db_path=db_path))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def make_client(db_path):
    """按需覆盖配置（例如把上传上限调小）的客户端工厂。"""

    def _make(**overrides):
        settings = Settings(db_path=db_path, **overrides)
        return TestClient(create_app(settings))

    return _make


def create_world(client, name="潮汐档案馆", description="一座建在退潮线上的档案馆。"):
    response = client.post("/api/v1/worlds", json={"name": name, "description": description})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, world_id, filename, data, content_type="text/markdown"):
    return client.post(
        f"/api/v1/worlds/{world_id}/documents",
        files={"file": (filename, data, content_type)},
    )