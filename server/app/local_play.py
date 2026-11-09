"""Serve the built game and its API on the phone's loopback interface."""

from __future__ import annotations

import argparse
import base64
from dataclasses import replace
import os
from pathlib import Path
import secrets

import uvicorn
from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from .errors import ApiError

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8795
LOCAL_ORIGIN = f"http://{LOCAL_HOST}:{LOCAL_PORT}"
LOCAL_USER = "player"


class LocalAccess:
    """Protect loopback data from other apps and cross-origin browser requests."""

    def __init__(self, app: ASGIApp, password: str, origin: str) -> None:
        self.app = app
        self.origin = origin
        self.credentials = b"Basic " + base64.b64encode(f"{LOCAL_USER}:{password}".encode())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "").encode("latin-1")
        if not secrets.compare_digest(authorization, self.credentials):
            error = ApiError(401, "LOCAL_LOGIN_REQUIRED", "请使用手机终端显示的本机密码登录。")
            response = JSONResponse(
                status_code=error.status, content=error.body(),
                headers={"WWW-Authenticate": 'Basic realm="Mojing local"', "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and headers.get("origin") != self.origin:
            error = ApiError(403, "ORIGIN_FORBIDDEN", "请从墨境的本机网页内发起操作。")
            await JSONResponse(status_code=error.status, content=error.body())(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["Cache-Control"] = "no-store"
                response_headers["X-Frame-Options"] = "DENY"
                response_headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def attach_local_ui(app: FastAPI, dist_dir: Path, password: str, port: int = LOCAL_PORT) -> None:
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="local-ui")
    app.add_middleware(LocalAccess, password=password, origin=f"http://{LOCAL_HOST}:{port}")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[LOCAL_HOST])


def read_or_create_password(path: Path) -> str:
    import fcntl  # The local launcher runs in Linux userspace.

    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), "r+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        password = file.read().strip()
        if not password:
            password = secrets.token_urlsafe(18)
            file.seek(0)
            file.write(password + "\n")
            file.truncate()
        return password


def create_local_app(
    dist_dir: Path, data_dir: Path, password: str, port: int = LOCAL_PORT,
    *, https_model_only: bool = False, model_profile=None,
) -> FastAPI:
    if not (dist_dir / "index.html").is_file():
        raise ValueError("缺少前端构建产物，请先执行安装命令。")
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["MOJING_DB_PATH"] = str(data_dir / "mojing.db")
    os.environ.pop("MOJING_PUBLIC_ORIGIN", None)

    from .config import load_settings
    from .main import create_app

    from .model_gateway import ModelSessionStore

    app = create_app(
        replace(load_settings(), cors_origins=(), https_model_only=https_model_only),
        narration_store=ModelSessionStore(ttl_seconds=None) if model_profile is not None else None,
        model_profile=model_profile,
    )
    attach_local_ui(app, dist_dir, password, port)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="在本机运行墨境")
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if not (args.dist / "index.html").is_file():
        parser.error("缺少前端构建产物，请先执行安装命令。")
    args.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    password = read_or_create_password(args.data_dir / "browser-password")
    app = create_local_app(args.dist, args.data_dir, password)

    print(f"在这部手机的浏览器打开 {LOCAL_ORIGIN}", flush=True)
    print(f"本机登录用户名：{LOCAL_USER}   密码：{password}", flush=True)
    print("请保留此 Termux 会话。结束游玩后按 Ctrl+C 停止。", flush=True)
    uvicorn.run(app, host=LOCAL_HOST, port=LOCAL_PORT, proxy_headers=False)


if __name__ == "__main__":
    main()
