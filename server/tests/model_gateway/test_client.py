"""OpenAIModelClient 单元测试（MockTransport、错误码映射、安全截断与契约校验）。"""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from app.model_gateway.client import OpenAIModelClient
from app.model_gateway.errors import GatewayError
from app.narration.core import (
    ContextSource,
    DraftError,
    NarrationRequest,
    generate_draft,
)


class TestOpenAIModelClient(unittest.IsolatedAsyncioTestCase):
    """测试 OpenAI 兼容客户端调用行为。"""

    def setUp(self) -> None:
        self.api_key = "sk-secret-test-key-999"
        self.base_url = "https://api.openai.com/v1"
        self.model = "gpt-4o"
        self.request = NarrationRequest(
            action="查看前方的石碑文字",
            sources=(
                ContextSource(source_id="chunk:c1", kind="chunk", text="石碑上刻着古老的雾港铭文。"),
                ContextSource(source_id="entry:e1", kind="entry", text="雾港是一个常年被迷雾笼罩的港口。"),
            ),
        )

    def test_client_repr_protects_key(self) -> None:
        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
        )
        self.assertNotIn(self.api_key, repr(client))
        self.assertNotIn(self.api_key, str(client))

    async def test_generate_success_and_draft_validation(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            self.assertEqual(req.method, "POST")
            self.assertEqual(str(req.url), "https://api.openai.com/v1/chat/completions")
            self.assertEqual(req.headers["Authorization"], f"Bearer {self.api_key}")

            body = json.loads(req.content.decode("utf-8"))
            self.assertEqual(body["model"], "gpt-4o")
            # 验证系统提示词中包含不可信数据防注入说明
            self.assertIn("不可信的背景参考数据", body["messages"][0]["content"])
            # 验证用户提示词中包含资料标识与玩家行动
            self.assertIn("chunk:c1", body["messages"][1]["content"])
            self.assertIn("查看前方的石碑文字", body["messages"][1]["content"])

            resp_payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({
                                "narrative": "你走近石碑，指尖拂过冰凉的石面，铭文在薄雾中若隐若现。",
                                "references": ["chunk:c1", "entry:e1"],
                            }),
                        }
                    }
                ]
            }
            return httpx.Response(200, json=resp_payload)

        transport = httpx.MockTransport(handler)
        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=transport,
        )

        # 1. 直接调用 client.generate
        raw_res = await client.generate(self.request)
        self.assertIsInstance(raw_res, dict)
        self.assertEqual(raw_res["references"], ["chunk:c1", "entry:e1"])

        # 2. 结合 Codex 核心 generate_draft 执行完整契约校验
        draft = await generate_draft(self.request, client)
        self.assertEqual(
            draft.narrative,
            "你走近石碑，指尖拂过冰凉的石面，铭文在薄雾中若隐若现。",
        )
        self.assertEqual(draft.references, ("chunk:c1", "entry:e1"))

    async def test_generate_strips_markdown_code_fence(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            content_with_fence = (
                "```json\n"
                + json.dumps({
                    "narrative": "雾气渐渐散开。",
                    "references": ["chunk:c1"],
                })
                + "\n```"
            )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": content_with_fence}}]},
            )

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )
        draft = await generate_draft(self.request, client)
        self.assertEqual(draft.narrative, "雾气渐渐散开。")
        self.assertEqual(draft.references, ("chunk:c1",))

    async def test_redirect_disallowed(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.example.com/steal"},
            )

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_REDIRECT_DISALLOWED")
        self.assertEqual(ctx.exception.status, 502)
        # 绝不泄漏目标跳转地址中的敏感信息
        self.assertNotIn(self.api_key, ctx.exception.message)

    async def test_auth_failure_401(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "Invalid API Key"})

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_AUTH_FAILED")
        self.assertEqual(ctx.exception.status, 401)
        self.assertNotIn(self.api_key, ctx.exception.message)

    async def test_rate_limited_429(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "Rate limit reached"})

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_RATE_LIMITED")
        self.assertEqual(ctx.exception.status, 429)

    async def test_upstream_error_500(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_UPSTREAM_ERROR")
        self.assertEqual(ctx.exception.status, 502)

    async def test_timeout_mapping_504(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Request timed out after 60s")

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_TIMEOUT")
        self.assertEqual(ctx.exception.status, 504)

    async def test_network_connection_failed_502(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused by host")

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_CONNECTION_FAILED")
        self.assertEqual(ctx.exception.status, 502)

    async def test_response_too_large_aborts_reading(self) -> None:
        # 产生超过 256KiB 的超大响应体
        huge_bytes = b"x" * (256 * 1024 + 100)

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=huge_bytes)

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
            max_response_bytes=256 * 1024,
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_RESPONSE_TOO_LARGE")
        self.assertEqual(ctx.exception.status, 502)

    async def test_invalid_json_content_502(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "【非JSON文本输出】"}}]},
            )

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_OUTPUT_NOT_JSON")
        self.assertEqual(ctx.exception.status, 502)

    async def test_empty_response_502(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(GatewayError) as ctx:
            await client.generate(self.request)
        self.assertEqual(ctx.exception.code, "MODEL_EMPTY_RESPONSE")
        self.assertEqual(ctx.exception.status, 502)

    async def test_preserves_extra_fields_for_codex_validation(self) -> None:
        # 模型私自输出了额外的 state_changes 字段
        def handler(req: httpx.Request) -> httpx.Response:
            payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({
                                "narrative": "你通过了暗道。",
                                "references": [],
                                "state_changes": {"hp": -10},
                            }),
                        }
                    }
                ]
            }
            return httpx.Response(200, json=payload)

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(handler),
        )

        # client.generate 应当诚实保留此字段交给 Codex 核心校验，而不是私自剔除
        raw_output = await client.generate(self.request)
        self.assertIn("state_changes", raw_output)

        # 经 Codex generate_draft 校验时必须被严格拦截为 MODEL_OUTPUT_INVALID
        with self.assertRaises(DraftError) as ctx:
            await generate_draft(self.request, client)
        self.assertEqual(ctx.exception.code, "MODEL_OUTPUT_INVALID")

    async def test_cancellation_propagates(self) -> None:
        async def slow_handler(req: httpx.Request) -> httpx.Response:
            await asyncio.sleep(10.0)
            return httpx.Response(200, json={})

        client = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(slow_handler),
        )

        task = asyncio.create_task(client.generate(self.request))
        await asyncio.sleep(0.01)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_client_test_success_and_failure(self) -> None:
        # 1. 成功测试
        def ok_handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode("utf-8"))
            self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "hello"}}]},
            )

        client_ok = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(ok_handler),
        )
        res = await client_ok.test()
        self.assertEqual(res, {"ok": True, "model": "gpt-4o"})

        # 2. 失败测试（如 401）
        def fail_handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "Unauthorized"})

        client_fail = OpenAIModelClient(
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            transport=httpx.MockTransport(fail_handler),
        )
        with self.assertRaises(GatewayError) as ctx:
            await client_fail.test()
        self.assertEqual(ctx.exception.code, "MODEL_AUTH_FAILED")
        self.assertEqual(ctx.exception.status, 401)
        self.assertNotIn(self.api_key, ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
