"""
阿里无影云手机后端（AgentBay Mobile Use）。

把云端 Android session 包装成 ActionBackend：
- screenshot() — 调 mobile.beta_take_screenshot 拿 raw bytes，base64 后返回，同时缓存屏幕尺寸供坐标换算
- execute_action() — 把 click/scroll/paste/... 这些原子动作映射到 mobile.tap/swipe/input_text/send_key

设计要点：
- Session 懒加载：构造 backend 不创建 session，第一次真正用时再 create()，避免无谓计费
- 坐标系统：上层 LLM 仍输出 0-1000 归一化坐标，本类内部按当前截图尺寸换算成像素
- 不可用动作（mobile 上无意义的 move/copy）返回 ok=True 但 message 标注，不阻塞 LLM 链路
- SDK 懒导入：仅在真正使用 AgentBay 时才 import wuying_agentbay_sdk，保证未装 SDK 的本地模式不受影响
"""

from __future__ import annotations

import base64
import time
from typing import Any

import utils.logger as logger

# Android 按键码（与 mobile.send_key 文档一致）
_KEY_HOME = 3
_KEY_BACK = 4
_KEY_VOLUME_UP = 24
_KEY_VOLUME_DOWN = 25
_KEY_POWER = 26
_KEY_MENU = 82

# 通用快捷键到 Android 按键码的映射（mobile 不支持组合键）
_HOTKEY_TO_ANDROID = {
    "esc": _KEY_BACK,
    "escape": _KEY_BACK,
    "back": _KEY_BACK,
    "home": _KEY_HOME,
    "menu": _KEY_MENU,
}

# 滚动手势的像素步长：LLM 的 1 click 大约对应竖向 120px 滑动
_SCROLL_PIXELS_PER_CLICK = 120
# 滚动手势的最大像素距离（防止 LLM 给一个夸张的 clicks 数）
_SCROLL_MAX_PIXELS = 1600
# 滚动手势的时长（ms）
_SCROLL_DURATION_MS = 300
# 拖拽手势的时长（ms）
_DRAG_DURATION_MS = 500
# 双击间隔（ms）
_DBLCLICK_INTERVAL_MS = 80


class AgentBayBackend:
    """阿里无影云手机后端。

    依赖 `wuying-agentbay-sdk`（懒导入）。第一次调用 screenshot/execute_action
    时才真正建立云端 session。
    """

    def __init__(
        self,
        api_key: str,
        image_id: str = "mobile_latest",
        screenshot_format: str = "jpeg",
    ):
        if not api_key:
            raise RuntimeError(
                "AgentBayBackend 需要 api_key（通过 AGENTBAY_API_KEY 环境变量或 config 传入）"
            )
        self._api_key = api_key
        self._image_id = image_id
        fmt = screenshot_format.strip().lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in ("png", "jpeg"):
            raise RuntimeError(f"AgentBay screenshot_format 必须是 png 或 jpeg，收到 {screenshot_format!r}")
        self._screenshot_format = fmt
        # 懒加载状态
        self._client: Any = None
        self._session: Any = None
        # 截图后缓存的屏幕尺寸（用于 0-1000 → 像素换算）
        self._screen_w: int = 0
        self._screen_h: int = 0

    # ── 公共接口（满足 ActionBackend Protocol）────────────────

    def screenshot(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        # mobile 没有"光标"概念，include_cursor 参数被忽略
        session = self.ensure_session(trace_id)
        result = session.mobile.beta_take_screenshot(format=self._screenshot_format)
        if not result.success:
            raise RuntimeError(f"AgentBay 截图失败: {result.error_message}")

        # 缓存屏幕尺寸，供 execute_action 做坐标换算
        if result.width and result.height:
            self._screen_w = int(result.width)
            self._screen_h = int(result.height)

        b64 = base64.b64encode(result.data).decode("utf-8")
        logger.info(
            {
                "msg": "AgentBay 截图成功",
                "size": f"{self._screen_w}x{self._screen_h}",
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

        logger.info(
            {"msg": "AgentBay 执行动作", "method": method, "params": params},
            trace_id,
        )

        if not method:
            return {"ok": False, "message": "缺少 action 方法名", "finish": False}

        try:
            session = self.ensure_session(trace_id)
            mobile = session.mobile

            match method:
                case "click":
                    return self._do_tap(mobile, params, finish)
                case "dblclick":
                    return self._do_double_tap(mobile, params, finish)
                case "move":
                    # mobile 无光标，move 在云端无意义；不报错以免打断 LLM 链路
                    return {"ok": True, "message": "云手机无光标，move 已忽略", "finish": finish}
                case "scroll":
                    return self._do_scroll(mobile, params, finish)
                case "drag":
                    return self._do_drag(mobile, params, finish)
                case "paste":
                    return self._do_paste(mobile, params, finish)
                case "copy":
                    return {"ok": True, "message": "云手机无系统剪贴板，copy 已忽略", "finish": finish}
                case "wait":
                    return self._do_wait(params, finish)
                case "hotkey":
                    return self._do_hotkey(mobile, params, finish)
                case _:
                    return {
                        "ok": False,
                        "message": f"AgentBay 不支持的动作: {method}",
                        "finish": finish,
                    }
        except Exception as e:
            logger.error(
                {"msg": "AgentBay 动作执行失败", "method": method, "error": str(e)},
                trace_id,
            )
            return {"ok": False, "message": f"AgentBay 执行报错: {e}", "finish": finish}

    # ── 内部：动作实现 ────────────────────────────────────────

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
        # 起点：优先 (x, y)，否则取 (x1, y1)，再否则用屏幕中心
        sx, sy = self._scroll_anchor(params)
        # 距离：与 macOS 一致（正向 clicks 对应"内容上滚 / 看见上方"），
        # 在 mobile 上即手指向下滑（end_y > start_y）。
        distance = min(abs(clicks) * _SCROLL_PIXELS_PER_CLICK, _SCROLL_MAX_PIXELS)
        ey = sy + distance if clicks > 0 else sy - distance
        # 防越界
        ey = max(0, min(ey, max(self._screen_h - 1, 0)))
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
        # 组合键在云手机上没有等价物，单键才能映射
        if "+" in keys:
            return {
                "ok": False,
                "message": f"云手机不支持组合键 {keys!r}",
                "finish": finish,
            }
        code = _HOTKEY_TO_ANDROID.get(keys.lower())
        if code is None:
            return {
                "ok": False,
                "message": f"云手机未映射的快捷键 {keys!r}（仅支持 esc/back/home/menu）",
                "finish": finish,
            }
        result = mobile.send_key(code)
        return self._wrap(result, f"已发送按键 {keys}", finish)

    # ── 公共：session 获取（供 AliyunMobileAgentStrategy 复用）

    def ensure_session(self, trace_id: str):
        """懒加载并返回 AgentBay session。

        多次调用复用同一个 session，避免重复计费。原子动作执行（screenshot/
        execute_action）和上层 Strategy 共用此 session。
        """
        if self._session is not None:
            return self._session

        try:
            from agentbay import AgentBay, CreateSessionParams
        except ImportError as e:  # pragma: no cover - 环境依赖
            raise RuntimeError(
                "未安装 wuying-agentbay-sdk。请执行 `pip install wuying-agentbay-sdk`"
            ) from e

        logger.info(
            {"msg": "正在创建 AgentBay session", "image_id": self._image_id},
            trace_id,
        )
        client = AgentBay(api_key=self._api_key)
        result = client.create(CreateSessionParams(image_id=self._image_id))
        session = getattr(result, "session", None) or result
        if session is None:
            raise RuntimeError("AgentBay session 创建失败：返回为空")
        self._client = client
        self._session = session
        logger.info(
            {"msg": "AgentBay session 已就绪", "session_id": getattr(session, "session_id", "?")},
            trace_id,
        )
        return self._session

    def _llm_to_pixel(self, params: dict) -> tuple[int, int]:
        x_val = _get(params, "x")
        y_val = _get(params, "y")
        if x_val is None or y_val is None:
            raise KeyError(f"缺少坐标参数 x 或 y，收到: {list(params.keys())}")
        if not (self._screen_w and self._screen_h):
            raise RuntimeError(
                "AgentBay 屏幕尺寸未知，请先调用一次 screenshot() 缓存尺寸"
            )
        px = int(round(float(x_val) * self._screen_w / 1000))
        py = int(round(float(y_val) * self._screen_h / 1000))
        # clamp 到屏幕边界
        px = max(0, min(px, self._screen_w - 1))
        py = max(0, min(py, self._screen_h - 1))
        return px, py

    def _scroll_anchor(self, params: dict) -> tuple[int, int]:
        x = _get(params, "x") if _get(params, "x") is not None else _get(params, "x1")
        y = _get(params, "y") if _get(params, "y") is not None else _get(params, "y1")
        if x is None or y is None:
            # 缺省锚点：屏幕中心
            return (self._screen_w // 2, self._screen_h // 2)
        return self._llm_to_pixel({"x": x, "y": y})

    @staticmethod
    def _wrap(sdk_result, success_message: str, finish: bool) -> dict:
        ok = bool(getattr(sdk_result, "success", False))
        msg = success_message if ok else (getattr(sdk_result, "error_message", "") or "AgentBay 调用失败")
        return {"ok": ok, "message": msg, "finish": finish}


# ─── 参数取值（兼容 LLM 脏数据键名，与 tools.actions.get_param 行为一致）

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
