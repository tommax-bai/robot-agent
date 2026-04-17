"""
阿里无影云手机后端（AgentBay Mobile Use）— 纯动作执行层。

职责：把 click/scroll/paste/... 原子动作映射到 mobile.tap/swipe/input_text/send_key，
以及截屏 → base64。Session 生命周期由注入的 SessionManager 管理。
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Any

import utils.logger as logger
from tools.backends.session_manager import is_session_dead_error

if TYPE_CHECKING:
    from tools.backends.session_manager import SessionManager

# Android 按键码
_KEY_HOME = 3
_KEY_BACK = 4
_KEY_VOLUME_UP = 24
_KEY_VOLUME_DOWN = 25
_KEY_POWER = 26
_KEY_MENU = 82

_HOTKEY_TO_ANDROID = {
    "esc": _KEY_BACK,
    "escape": _KEY_BACK,
    "back": _KEY_BACK,
    "home": _KEY_HOME,
    "menu": _KEY_MENU,
}

_SCROLL_PIXELS_PER_CLICK = 120
_SCROLL_MAX_PIXELS = 1600
_SCROLL_DURATION_MS = 300
_DRAG_DURATION_MS = 500
_DBLCLICK_INTERVAL_MS = 80


class AgentBayBackend:
    """阿里无影云手机后端。Session 由 SessionManager 懒加载管理。"""

    _ACTION_HANDLERS: dict[str, str] = {
        "click": "_do_tap",
        "dblclick": "_do_double_tap",
        "scroll": "_do_scroll",
        "drag": "_do_drag",
        "paste": "_do_paste",
        "wait": "_do_wait",
        "hotkey": "_do_hotkey",
    }
    _NOOP_ACTIONS: dict[str, str] = {
        "move": "云手机无光标，move 已忽略",
        "copy": "云手机无系统剪贴板，copy 已忽略",
    }

    def __init__(
        self,
        session_mgr: SessionManager,
        screenshot_format: str = "jpeg",
        mode: str = "agentbay",
    ):
        self._session_mgr = session_mgr
        self._mode = mode
        fmt = screenshot_format.strip().lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in ("png", "jpeg"):
            raise RuntimeError(f"screenshot_format 必须是 png 或 jpeg，收到 {screenshot_format!r}")
        self._screenshot_format = fmt

    # ── ActionBackend Protocol ────────────────────────────────

    def screenshot(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        session = self._session_mgr.acquire(trace_id)
        try:
            result = session.mobile.beta_take_screenshot(format=self._screenshot_format)
        except Exception as e:
            if is_session_dead_error(e):
                self._session_mgr.mark_dead("screenshot", str(e), trace_id)
            raise
        if not result.success:
            if is_session_dead_error(RuntimeError(result.error_message)):
                self._session_mgr.mark_dead("screenshot", result.error_message, trace_id)
            raise RuntimeError(f"[{self._mode}] 截图失败: {result.error_message}")

        if result.width and result.height:
            self._session_mgr.update_screen_size(int(result.width), int(result.height))

        b64 = base64.b64encode(result.data).decode("utf-8")
        w, h = self._session_mgr.screen_size
        logger.debug(
            {
                "msg": f"[{self._mode}] 截图成功",
                "size": f"{w}x{h}",
                "bytes": len(result.data),
                "format": self._screenshot_format,
            },
            trace_id,
        )
        return b64, 0, 0

    def execute_action(self, trace_id: str, action: dict) -> dict:
        method = action.get("method")
        params = action.get("params", {}) or {}
        finish = bool(action.get("finish", False))

        logger.debug(
            {"msg": f"[{self._mode}] 执行动作", "method": method, "params": params},
            trace_id,
        )

        if not method:
            return {"ok": False, "message": "缺少 action 方法名", "finish": False}

        try:
            if method in self._NOOP_ACTIONS:
                return {"ok": True, "message": self._NOOP_ACTIONS[method], "finish": finish}

            handler_name = self._ACTION_HANDLERS.get(method)
            if handler_name is None:
                return {"ok": False, "message": f"[{self._mode}] 不支持的动作: {method}", "finish": finish}

            session = self._session_mgr.acquire(trace_id)
            mobile = session.mobile

            if method == "wait":
                return self._do_wait(params, finish)
            return getattr(self, handler_name)(mobile, params, finish)
        except Exception as e:
            if is_session_dead_error(e):
                self._session_mgr.mark_dead("execute_action", str(e), trace_id)
                return {
                    "ok": False,
                    "message": f"云端 session 已失效（已清理本地引用，下次操作会重建）: {e}",
                    "finish": finish,
                }
            logger.error(
                {"msg": f"[{self._mode}] 动作执行失败", "method": method, "error": str(e)},
                trace_id,
            )
            return {"ok": False, "message": f"[{self._mode}] 执行报错: {e}", "finish": finish}

    # ── 动作实现 ──────────────────────────────────────────────

    def _do_tap(self, mobile, params: dict, finish: bool) -> dict:
        x, y = self._llm_to_pixel(params)
        result = mobile.tap(x, y)
        return self._wrap(result, f"已点击 ({x}, {y})", finish)

    def _do_double_tap(self, mobile, params: dict, finish: bool) -> dict:
        x, y = self._llm_to_pixel(params)
        r1 = mobile.tap(x, y)
        if not r1.success:
            return self._wrap(r1, f"双击第一次失败 ({x}, {y})", finish)
        time.sleep(_DBLCLICK_INTERVAL_MS / 1000.0)
        r2 = mobile.tap(x, y)
        return self._wrap(r2, f"已双击 ({x}, {y})", finish)

    def _do_scroll(self, mobile, params: dict, finish: bool) -> dict:
        clicks = int(_get(params, "clicks", 1))
        sx, sy = self._scroll_anchor(params)
        distance = min(abs(clicks) * _SCROLL_PIXELS_PER_CLICK, _SCROLL_MAX_PIXELS)
        ey = sy + distance if clicks > 0 else sy - distance
        _, sh = self._session_mgr.screen_size
        ey = max(0, min(ey, max(sh - 1, 0)))
        result = mobile.swipe(sx, sy, sx, ey, duration_ms=_SCROLL_DURATION_MS)
        direction = "上" if clicks > 0 else "下"
        return self._wrap(result, f"滑动({direction} 强度{abs(clicks)})", finish)

    def _do_drag(self, mobile, params: dict, finish: bool) -> dict:
        x1, y1 = self._llm_to_pixel({"x": _get(params, "x1"), "y": _get(params, "y1")})
        x2, y2 = self._llm_to_pixel({"x": _get(params, "x2"), "y": _get(params, "y2")})
        result = mobile.swipe(x1, y1, x2, y2, duration_ms=_DRAG_DURATION_MS)
        return self._wrap(result, f"已拖拽 ({x1},{y1})→({x2},{y2})", finish)

    def _do_paste(self, mobile, params: dict, finish: bool) -> dict:
        text = str(_get(params, "text", ""))
        if not text:
            return {"ok": True, "message": "paste 文本为空，已跳过", "finish": finish}
        result = mobile.input_text(text)
        preview = text[:20] + "…" if len(text) > 20 else text
        return self._wrap(result, f'已输入 "{preview}"', finish)

    @staticmethod
    def _do_wait(params: dict, finish: bool) -> dict:
        ms = float(_get(params, "milliseconds", 0))
        time.sleep(max(ms, 0) / 1000.0)
        return {"ok": True, "message": f"已等待 {ms:.0f}ms", "finish": finish}

    def _do_hotkey(self, mobile, params: dict, finish: bool) -> dict:
        keys = str(_get(params, "keys", "")).strip()
        if "+" in keys:
            return {"ok": False, "message": f"云手机不支持组合键 {keys!r}", "finish": finish}
        code = _HOTKEY_TO_ANDROID.get(keys.lower())
        if code is None:
            return {"ok": False, "message": f"云手机未映射的快捷键 {keys!r}（仅支持 esc/back/home/menu）", "finish": finish}
        result = mobile.send_key(code)
        return self._wrap(result, f"已发送按键 {keys}", finish)

    # ── 坐标转换 ──────────────────────────────────────────────

    def _llm_to_pixel(self, params: dict) -> tuple[int, int]:
        x_val = _get(params, "x")
        y_val = _get(params, "y")
        if x_val is None or y_val is None:
            raise KeyError(f"缺少坐标参数 x 或 y，收到: {list(params.keys())}")
        sw, sh = self._session_mgr.screen_size
        if not (sw and sh):
            raise RuntimeError(f"[{self._mode}] 屏幕尺寸未知，请先调用一次 screenshot() 缓存尺寸")
        px = int(round(float(x_val) * sw / 1000))
        py = int(round(float(y_val) * sh / 1000))
        px = max(0, min(px, sw - 1))
        py = max(0, min(py, sh - 1))
        return px, py

    def _scroll_anchor(self, params: dict) -> tuple[int, int]:
        x = _get(params, "x") if _get(params, "x") is not None else _get(params, "x1")
        y = _get(params, "y") if _get(params, "y") is not None else _get(params, "y1")
        if x is None or y is None:
            sw, sh = self._session_mgr.screen_size
            return (sw // 2, sh // 2)
        return self._llm_to_pixel({"x": x, "y": y})

    @staticmethod
    def _wrap(sdk_result, success_message: str, finish: bool) -> dict:
        ok = bool(getattr(sdk_result, "success", False))
        msg = success_message if ok else (getattr(sdk_result, "error_message", "") or "云手机 SDK 调用失败")
        return {"ok": ok, "message": msg, "finish": finish}


# ─── 孤儿清理（启动时，独立于 SessionManager）────────────────

def cleanup_orphan_sessions(api_key: str) -> int:
    """启动时拉取该 API key 下所有 session 并销毁。"""
    if not api_key:
        return 0
    try:
        from agentbay import AgentBay
        from agentbay._sync.session import Session as _Session
    except ImportError:
        return 0

    try:
        client = AgentBay(api_key=api_key)
        result = client.list()
    except Exception as e:
        logger.warning({"msg": "云手机 list session 失败，跳过孤儿清理", "error": str(e)})
        return 0

    raw = getattr(result, "session_ids", None) or []
    sids: list[str] = []
    for d in raw:
        if isinstance(d, dict):
            sid = d.get("sessionId") or d.get("SessionId")
            if sid:
                sids.append(sid)
        elif isinstance(d, str):
            sids.append(d)

    if not sids:
        logger.info({"msg": "云手机 无遗留 session，无需清理"})
        return 0

    logger.warning({"msg": f"检测到 {len(sids)} 个孤儿云手机 session，正在清理", "session_ids": sids})
    cleaned = 0
    for sid in sids:
        try:
            sess = _Session(client, sid)
            r = client.delete(sess)
            if bool(getattr(r, "success", True)):
                cleaned += 1
                logger.info({"msg": "孤儿 session 已销毁", "session_id": sid})
            else:
                logger.warning({"msg": "孤儿 session 销毁返回 success=False", "session_id": sid})
        except Exception as e:
            logger.warning({"msg": "孤儿 session 销毁异常", "session_id": sid, "error": str(e)})
    return cleaned


# ─── 参数取值（兼容 LLM 脏数据键名）────────────────────────

def _get(params: dict, key: str, default: Any = None) -> Any:
    if not isinstance(params, dict):
        return default
    if key in params:
        return params[key]
    for variant in (key.lower(), key.upper(), f'"{key}"', f"'{key}'"):
        if variant in params:
            return params[variant]
    for k in params.keys():
        clean = str(k).strip().strip('"').strip("'").lower()
        if clean == key.lower():
            return params[k]
    return default
