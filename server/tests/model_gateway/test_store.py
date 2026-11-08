"""ModelSessionStore 单元测试（生命周期、TTL、容量上限、安全脱敏）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.model_gateway.errors import GatewayError
from app.model_gateway.store import ModelSessionStore
from app.model_gateway.url import validate_and_normalize_base_url


class TestModelSessionStore(unittest.IsolatedAsyncioTestCase):
    """测试会话内存存储、TTL 与容量限制。"""

    def setUp(self) -> None:
        self.current_time = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

        def mock_clock() -> datetime:
            return self.current_time

        self.store = ModelSessionStore(
            ttl_seconds=1800,  # 30 分钟
            max_sessions=8,
            clock=mock_clock,
        )

    def test_url_validation_and_normalization(self) -> None:
        # 合法 URL
        self.assertEqual(
            validate_and_normalize_base_url("https://api.openai.com/v1/"),
            "https://api.openai.com/v1",
        )
        self.assertEqual(
            validate_and_normalize_base_url("http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434/v1",
        )
        self.assertEqual(
            validate_and_normalize_base_url("http://localhost:8000"),
            "http://localhost:8000",
        )

        # 非法协议
        with self.assertRaises(GatewayError) as ctx:
            validate_and_normalize_base_url("ftp://example.com/v1")
        self.assertEqual(ctx.exception.code, "INVALID_BASE_URL")
        self.assertEqual(ctx.exception.status, 422)

        # 包含 userinfo
        with self.assertRaises(GatewayError) as ctx:
            validate_and_normalize_base_url("https://user:pass@example.com/v1")
        self.assertEqual(ctx.exception.code, "INVALID_BASE_URL")

        # 包含 query
        with self.assertRaises(GatewayError) as ctx:
            validate_and_normalize_base_url("https://example.com/v1?token=123")
        self.assertEqual(ctx.exception.code, "INVALID_BASE_URL")

        # 包含 fragment
        with self.assertRaises(GatewayError) as ctx:
            validate_and_normalize_base_url("https://example.com/v1#part")
        self.assertEqual(ctx.exception.code, "INVALID_BASE_URL")

        # 空白或非字符串
        with self.assertRaises(GatewayError) as ctx:
            validate_and_normalize_base_url("   ")
        self.assertEqual(ctx.exception.code, "INVALID_BASE_URL")

    def test_create_session_success_and_no_key_leak(self) -> None:
        secret_key = "sk-super-secret-key-12345"
        resp = self.store.create(
            base_url="https://api.example.com/v1",
            model="gpt-4o",
            api_key=secret_key,
        )

        # 校验响应契约字段
        self.assertIn("session_id", resp)
        self.assertEqual(resp["model"], "gpt-4o")
        self.assertEqual(resp["expires_at"], "2026-11-08T12:30:00Z")

        # 严禁在返回结果中包含 Key
        self.assertNotIn("api_key", resp)
        self.assertNotIn(secret_key, str(resp))

        # 检查内部 session 对象的 repr，确保 repr=False
        session = self.store._sessions[resp["session_id"]]
        self.assertNotIn(secret_key, repr(session))
        self.assertNotIn(secret_key, str(session))

    def test_session_ttl_expiration_and_refresh(self) -> None:
        resp = self.store.create(
            base_url="https://api.example.com/v1",
            model="gpt-4o",
            api_key="sk-test",
        )
        sid = resp["session_id"]

        # 20 分钟后访问：未过期，且成功刷新 TTL 到当前时间 + 30 分钟
        self.current_time += timedelta(minutes=20)
        client = self.store.get_client(sid)
        self.assertEqual(client.model, "gpt-4o")

        session = self.store._sessions[sid]
        # 刷新后到期时间应该是 12:20 + 30m = 12:50
        self.assertEqual(session.expires_at, datetime(2026, 11, 8, 12, 50, 0, tzinfo=timezone.utc))

        # 再前进 31 分钟（当前 12:51），已超过 12:50
        self.current_time += timedelta(minutes=31)
        with self.assertRaises(GatewayError) as ctx:
            self.store.get_client(sid)
        self.assertEqual(ctx.exception.code, "MODEL_SESSION_EXPIRED")
        self.assertEqual(ctx.exception.status, 409)

        # 过期访问后，该会话应当已被自动清理
        self.assertNotIn(sid, self.store._sessions)

    def test_capacity_limit_max_8_sessions(self) -> None:
        # 创建 8 个会话
        session_ids = []
        for i in range(8):
            resp = self.store.create(
                base_url="https://api.example.com/v1",
                model=f"model-{i}",
                api_key=f"key-{i}",
            )
            session_ids.append(resp["session_id"])

        self.assertEqual(self.store.active_count, 8)

        # 尝试创建第 9 个会话：必须明确抛出错误，禁止静默踢掉旧会话
        with self.assertRaises(GatewayError) as ctx:
            self.store.create(
                base_url="https://api.example.com/v1",
                model="model-overflow",
                api_key="key-overflow",
            )
        self.assertEqual(ctx.exception.code, "MODEL_SESSION_CAPACITY_EXCEEDED")
        self.assertEqual(ctx.exception.status, 429)

        # 验证之前的 8 个会话全部安然无恙，未被偷偷踢出
        for sid in session_ids:
            self.assertIn(sid, self.store._sessions)

        # 删除其中一个会话后，允许创建新会话
        self.store.delete(session_ids[0])
        self.assertEqual(self.store.active_count, 7)

        resp9 = self.store.create(
            base_url="https://api.example.com/v1",
            model="model-9",
            api_key="key-9",
        )
        self.assertIn("session_id", resp9)
        self.assertEqual(self.store.active_count, 8)

    def test_expired_sessions_do_not_block_capacity(self) -> None:
        # 创建 8 个会话
        for i in range(8):
            self.store.create(
                base_url="https://api.example.com/v1",
                model=f"model-{i}",
                api_key=f"key-{i}",
            )

        # 时间流逝 31 分钟，这 8 个会话全部超时过期
        self.current_time += timedelta(minutes=31)

        # 创建新会话应自动触发对过期死会话的清理，成功创建
        resp = self.store.create(
            base_url="https://api.example.com/v1",
            model="model-fresh",
            api_key="key-fresh",
        )
        self.assertEqual(self.store.active_count, 1)
        self.assertIn(resp["session_id"], self.store._sessions)

    def test_delete_idempotence(self) -> None:
        resp = self.store.create(
            base_url="https://api.example.com/v1",
            model="gpt-4o",
            api_key="sk-test",
        )
        sid = resp["session_id"]

        # 首次删除
        self.store.delete(sid)
        self.assertNotIn(sid, self.store._sessions)

        # 重复删除相同会话，不报错，幂等完成
        self.store.delete(sid)
        self.store.delete("non-existent-session-id")

    def test_clear_all_sessions(self) -> None:
        self.store.create(base_url="https://api.example.com/v1", model="m1", api_key="k1")
        self.store.create(base_url="https://api.example.com/v1", model="m2", api_key="k2")
        self.assertEqual(self.store.active_count, 2)

        self.store.clear()
        self.assertEqual(self.store.active_count, 0)
        self.assertEqual(len(self.store._sessions), 0)

    def test_client_repr_does_not_contain_key(self) -> None:
        secret = "sk-very-secret-string-xyz"
        resp = self.store.create(
            base_url="https://api.example.com/v1",
            model="gpt-4o",
            api_key=secret,
        )
        client = self.store.get_client(resp["session_id"])
        self.assertNotIn(secret, repr(client))
        self.assertNotIn(secret, str(client))


if __name__ == "__main__":
    unittest.main()
