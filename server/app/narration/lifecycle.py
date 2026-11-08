"""Tie upstream work to the requesting connection, including ASGI disconnects."""

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from starlette.requests import Request

from ..errors import ApiError

T = TypeVar('T')


async def await_request_work(request: Request, work: Awaitable[T], timeout: float) -> T:
    async def disconnected() -> None:
        while not asyncio.current_task().cancelling():
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.05)

    task = asyncio.ensure_future(work)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({task, watcher}, timeout=timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        # Disconnect wins a simultaneous completion; never report a late success.
        if watcher in done:
            watcher.result()
            raise ApiError(499, 'REQUEST_CANCELLED', '连接已关闭，已停止生成。')
        if task not in done:
            raise TimeoutError()
        return task.result()
    finally:
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)
