"""
RemoteBackend: 通过 HTTP 调用 Session Service 的截图/动作接口。

实现 ActionBackend Protocol，让 Brain Service 的 VisionActionStep / ActionDispatcher
无感知地使用远程 session。
"""

from __future__ import annotations

import httpx

import utils.logger as logger


class RemoteBackend:
    """ActionBackend over HTTP — 调用 Session Service 的 API。"""

    def __init__(
        self,
        session_service_url: str,
        account_id: str,
        api_key: str = "",
        mode: str = "cloudmobile",
    ):
        self._base = session_service_url.rstrip("/")
        self._account_id = account_id
        self._mode = mode
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=self._base,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            headers=headers,
        )

    def screenshot(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        resp = self._client.post(
            "/api/v1/session/screenshot",
            json={
                "account_id": self._account_id,
                "trace_id": trace_id,
                "include_cursor": include_cursor,
            },
        )
        resp.raise_for_status()
        d = resp.json()
        if not d.get("ok", False):
            raise RuntimeError(f"[{self._mode}] 远程截图失败: {d.get('error', 'unknown')}")
        return d["base64"], d.get("cursor_x", 0), d.get("cursor_y", 0)

    def execute_action(self, trace_id: str, action: dict) -> dict:
        resp = self._client.post(
            "/api/v1/session/action",
            json={
                "account_id": self._account_id,
                "trace_id": trace_id,
                "action": action,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
