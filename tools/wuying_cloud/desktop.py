"""阿里无影云桌面执行环境（WuyingDesktopEnv，Windows/Linux）。

与 `WuyingMobileEnv` 并列的桌面实现：SessionManager 复用同一套 AgentBay 生命周期，
动作走 `session.computer.*`（MCP tool: click_mouse / drag_mouse / scroll /
input_text / press_keys / screenshot），不依赖 Android 的 `mobile.tap`。

LLM 端契约保持不变（click / dblclick / scroll / drag / paste / hotkey / wait /
finish + long_press/clear_input 兼容映射），由本类负责把 Android 语义翻成桌面语义。
"""

from __future__ import annotations

import base64
import time
from io import BytesIO
from typing import Any

from PIL import Image

import config
import utils.logger as logger
from tools.wuying_cloud.humanize import (
    jitter_xy,
    maybe_post_pause,
    maybe_pre_pause,
    type_chunked,
)
from tools.wuying_cloud.keymap import (
    DBLCLICK_INTERVAL_MS,
    SCROLL_MAX_PIXELS,
    SCROLL_PIXELS_PER_CLICK,
)
from tools.wuying_cloud.session import (
    SessionManager,
    is_session_dead_error,
)
from tools.environment import coerce_param
from tools.image_postprocess import encode_for_llm


# LLM-side hotkey names → pyautogui-风格键名 → 云桌面 press_keys
# 约定：combo 用 '+' 连接（如 ctrl+c / ctrl+shift+t），小写输入在下面归一化。
_HOTKEY_NAME_MAP: dict[str, str] = {
    "ctrl": "Ctrl", "control": "Ctrl",
    "alt": "Alt", "option": "Alt",
    "shift": "Shift",
    "cmd": "Meta", "command": "Meta", "win": "Meta", "super": "Meta", "meta": "Meta",
    "enter": "Enter", "return": "Enter",
    "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "Space", "backspace": "Backspace", "delete": "Delete", "del": "Delete",
    "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "capslock": "CapsLock",
}
for _n in range(1, 13):
    _HOTKEY_NAME_MAP[f"f{_n}"] = f"F{_n}"


def _map_key(name: str) -> str:
    n = name.strip().lower()
    if not n:
        return ""
    mapped = _HOTKEY_NAME_MAP.get(n)
    if mapped is not None:
        return mapped
    # 单字符直接传递；字母统一大写让 SDK 稳定一些（press_keys 对字母大小写宽容）
    if len(n) == 1:
        return n.upper() if n.isalpha() else n
    return name  # 保留原值


class WuyingDesktopEnv:
    """阿里无影云桌面执行环境（Windows/Linux 镜像）。"""

    _ACTION_HANDLERS: dict[str, str] = {
        "click": "_do_click",
        "dblclick": "_do_dblclick",
        "scroll": "_do_scroll",
        "drag": "_do_drag",
        "paste": "_do_paste",
        "wait": "_do_wait",
        "hotkey": "_do_hotkey",
        "clear_input": "_do_clear_input",
        # 桌面语义下 long_press = 长按左键 → 退化为 drag 起止同点；少见
        "long_press": "_do_long_press",
        # 桌面有真实光标 / 系统剪贴板，都支持
        "move": "_do_move",
        "copy": "_do_copy",
    }

    def __init__(self, session_mgr: SessionManager, mode: str = "agentbay"):
        self._session_mgr = session_mgr
        self._mode = mode
        fmt = config.settings.system.screenshot.wuyingcloud.format.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        self.content_type = f"image/{fmt}"

    # ── Environment 协议 ──────────────────────────────────────

    def capture(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        session = self._session_mgr.acquire(trace_id)
        try:
            result = session.computer.beta_take_screenshot(format="png")
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

        profile = config.settings.system.screenshot.wuyingcloud
        img = Image.open(BytesIO(result.data))
        data, content_type = encode_for_llm(img, profile)
        self.content_type = content_type

        b64 = base64.b64encode(data).decode("utf-8")
        w, h = self._session_mgr.screen_size
        logger.debug(
            {
                "msg": f"[{self._mode}] 桌面截图成功",
                "size": f"{w}x{h}",
                "raw_bytes": len(result.data),
                "encoded_bytes": len(data),
                "content_type": content_type,
            },
            trace_id,
        )
        return b64, 0, 0

    def perform(self, trace_id: str, action: dict) -> dict:
        method = action.get("method")
        params = action.get("params", {}) or {}
        finish = bool(action.get("finish", False))

        logger.debug(
            {"msg": f"[{self._mode}] 执行桌面动作", "method": method, "params": params},
            trace_id,
        )

        if not method:
            return {"ok": False, "message": "缺少 action 方法名", "finish": False}

        try:
            handler_name = self._ACTION_HANDLERS.get(method)
            if handler_name is None:
                return {"ok": False, "message": f"[{self._mode}] 不支持的动作: {method}", "finish": finish}

            if method == "wait":
                return self._do_wait(params, finish)

            session = self._session_mgr.acquire(trace_id)
            computer = session.computer
            return getattr(self, handler_name)(computer, params, finish)
        except Exception as e:
            if is_session_dead_error(e):
                self._session_mgr.mark_dead("execute_action", str(e), trace_id)
                return {
                    "ok": False,
                    "message": f"云端 session 已失效（已清理本地引用，下次操作会重建）: {e}",
                    "finish": finish,
                }
            logger.error(
                {"msg": f"[{self._mode}] 桌面动作执行失败", "method": method, "error": str(e)},
                trace_id,
            )
            return {"ok": False, "message": f"[{self._mode}] 执行报错: {e}", "finish": finish}

    # ── 动作实现 ──────────────────────────────────────────────

    def _do_click(self, computer, params: dict, finish: bool) -> dict:
        x, y = self._llm_to_pixel(params)
        jx, jy = jitter_xy(x, y, config.settings.system.input.tap_jitter_px)
        maybe_pre_pause()
        result = computer.click_mouse(jx, jy, "left")
        maybe_post_pause()
        return self._wrap(result, f"已点击 ({jx}, {jy})", finish)

    def _do_dblclick(self, computer, params: dict, finish: bool) -> dict:
        x, y = self._llm_to_pixel(params)
        jx, jy = jitter_xy(x, y, config.settings.system.input.tap_jitter_px)
        maybe_pre_pause()
        result = computer.click_mouse(jx, jy, "double_left")
        maybe_post_pause()
        return self._wrap(result, f"已双击 ({jx}, {jy})", finish)

    def _do_move(self, computer, params: dict, finish: bool) -> dict:
        x, y = self._llm_to_pixel(params)
        maybe_pre_pause()
        result = computer.move_mouse(x, y)
        maybe_post_pause()
        return self._wrap(result, f"已移动鼠标到 ({x}, {y})", finish)

    def _do_scroll(self, computer, params: dict, finish: bool) -> dict:
        clicks = int(coerce_param(params, "clicks", 1))
        sx, sy = self._scroll_anchor(params)
        # LLM 侧约定：clicks>0 向上（内容上移），负数向下；桌面 scroll direction 直接传
        direction = "up" if clicks > 0 else "down"
        amount = min(abs(clicks), max(1, SCROLL_MAX_PIXELS // max(SCROLL_PIXELS_PER_CLICK, 1)))
        maybe_pre_pause()
        result = computer.scroll(sx, sy, direction, amount)
        maybe_post_pause()
        return self._wrap(result, f"滑动({direction} 强度{abs(clicks)})", finish)

    def _do_drag(self, computer, params: dict, finish: bool) -> dict:
        x1, y1 = self._llm_to_pixel(
            {"x": coerce_param(params, "x1"), "y": coerce_param(params, "y1")}
        )
        x2, y2 = self._llm_to_pixel(
            {"x": coerce_param(params, "x2"), "y": coerce_param(params, "y2")}
        )
        maybe_pre_pause()
        result = computer.drag_mouse(x1, y1, x2, y2, "left")
        maybe_post_pause()
        return self._wrap(result, f"已拖拽 ({x1},{y1})→({x2},{y2})", finish)

    def _do_paste(self, computer, params: dict, finish: bool) -> dict:
        text = str(coerce_param(params, "text", ""))
        if not text:
            return {"ok": True, "message": "paste 文本为空，已跳过", "finish": finish}
        in_cfg = config.settings.system.input
        preview = text[:20] + "…" if len(text) > 20 else text
        maybe_pre_pause()

        # 拟人化：逐字符 input_text + 随机 [min,max]ms 抖动；默认关
        if in_cfg.paste_per_char:
            ok, err, sent = type_chunked(
                computer.input_text, text,
                min_ms=in_cfg.paste_min_delay_ms,
                max_ms=in_cfg.paste_max_delay_ms,
            )
            maybe_post_pause()
            if ok:
                return {
                    "ok": True,
                    "finish": finish,
                    "message": f'已逐字输入 "{preview}"（{sent}字）',
                }
            return {
                "ok": False,
                "finish": finish,
                "message": f"逐字输入第 {sent + 1}/{len(text)} 字符失败：{err}",
            }

        result = computer.input_text(text)
        maybe_post_pause()
        return self._wrap(result, f'已输入 "{preview}"', finish)

    @staticmethod
    def _do_wait(params: dict, finish: bool) -> dict:
        ms = float(coerce_param(params, "milliseconds", 0))
        time.sleep(max(ms, 0) / 1000.0)
        return {"ok": True, "message": f"已等待 {ms:.0f}ms", "finish": finish}

    def _do_hotkey(self, computer, params: dict, finish: bool) -> dict:
        keys_raw = str(coerce_param(params, "keys", "")).strip()
        if not keys_raw:
            return {"ok": False, "message": "hotkey 缺少 keys", "finish": finish}
        parts = [p for p in keys_raw.replace(" ", "").split("+") if p]
        mapped = [_map_key(p) for p in parts]
        if not all(mapped):
            return {"ok": False, "message": f"hotkey 存在无法识别的键: {keys_raw!r}", "finish": finish}
        maybe_pre_pause()
        result = computer.press_keys(mapped, hold=False)
        maybe_post_pause()
        return self._wrap(result, f"已发送快捷键 {'+'.join(mapped)}", finish)

    def _do_clear_input(self, computer, params: dict, finish: bool) -> dict:
        """桌面端：click 聚焦 → ctrl+a → delete，一把梭。"""
        x, y = self._llm_to_pixel(params)
        r1 = computer.click_mouse(x, y, "left")
        if not r1.success:
            return {"ok": False, "finish": finish,
                    "message": f"clear_input 第一步点击失败：{r1.error_message}"}
        time.sleep(DBLCLICK_INTERVAL_MS / 1000.0)
        r2 = computer.press_keys(["Ctrl", "A"], hold=False)
        if not r2.success:
            return {"ok": False, "finish": finish,
                    "message": f"clear_input 全选失败：{r2.error_message}"}
        r3 = computer.press_keys(["Delete"], hold=False)
        return self._wrap(r3, f"已清空 ({x}, {y}) 所在输入框", finish)

    def _do_long_press(self, computer, params: dict, finish: bool) -> dict:
        """桌面端 long_press 不常用；退化为原地短拖以保持按键 = 悬停动作的语义。"""
        x, y = self._llm_to_pixel(params)
        jx, jy = jitter_xy(x, y, config.settings.system.input.tap_jitter_px)
        maybe_pre_pause()
        result = computer.drag_mouse(jx, jy, jx, jy, "left")
        maybe_post_pause()
        return self._wrap(result, f"已在 ({jx}, {jy}) 长按（等效）", finish)

    def _do_copy(self, computer, params: dict, finish: bool) -> dict:
        """ctrl+c。桌面有系统剪贴板，可真正 copy。"""
        maybe_pre_pause()
        result = computer.press_keys(["Ctrl", "C"], hold=False)
        maybe_post_pause()
        return self._wrap(result, "已发送 Ctrl+C", finish)

    # ── 坐标转换 ──────────────────────────────────────────────

    def _llm_to_pixel(self, params: dict) -> tuple[int, int]:
        x_val = coerce_param(params, "x")
        y_val = coerce_param(params, "y")
        if x_val is None or y_val is None:
            raise KeyError(f"缺少坐标参数 x 或 y，收到: {list(params.keys())}")
        sw, sh = self._session_mgr.screen_size
        if not (sw and sh):
            raise RuntimeError(f"[{self._mode}] 屏幕尺寸未知，请先调用一次 capture() 缓存尺寸")
        px = int(round(float(x_val) * sw / 1000))
        py = int(round(float(y_val) * sh / 1000))
        px = max(0, min(px, sw - 1))
        py = max(0, min(py, sh - 1))
        return px, py

    def _scroll_anchor(self, params: dict) -> tuple[int, int]:
        x = coerce_param(params, "x") if coerce_param(params, "x") is not None else coerce_param(params, "x1")
        y = coerce_param(params, "y") if coerce_param(params, "y") is not None else coerce_param(params, "y1")
        if x is None or y is None:
            sw, sh = self._session_mgr.screen_size
            return (sw // 2, sh // 2)
        return self._llm_to_pixel({"x": x, "y": y})

    @staticmethod
    def _wrap(sdk_result: Any, success_message: str, finish: bool) -> dict:
        ok = bool(getattr(sdk_result, "success", False))
        msg = success_message if ok else (getattr(sdk_result, "error_message", "") or "云桌面 SDK 调用失败")
        return {"ok": ok, "message": msg, "finish": finish}


__all__ = ["WuyingDesktopEnv"]
