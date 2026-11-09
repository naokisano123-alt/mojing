"""Run the existing adventure engine inside the Android application process."""

from pathlib import Path
import secrets
import socket
from threading import Thread
import time

import uvicorn

from app.local_play import LOCAL_HOST, create_local_app


def start(dist: str, data: str, port: int, model_profile=None) -> str:
    password = secrets.token_urlsafe(24)
    app = create_local_app(Path(dist), Path(data), password, port, https_model_only=True, model_profile=model_profile)
    server = uvicorn.Server(uvicorn.Config(
        app, host=LOCAL_HOST, port=port, loop="asyncio", http="h11",
        ws="none", proxy_headers=False, access_log=False,
    ))
    # Hold the bound socket from the first bind so a port conflict can never
    # send the WebView or its local credentials to another process.
    listener = socket.socket()
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LOCAL_HOST, port))
        listener.listen(64)
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 12
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("墨境本机引擎启动失败。")
            time.sleep(0.025)
    except BaseException:
        server.should_exit = True
        listener.close()
        raise
    return password
