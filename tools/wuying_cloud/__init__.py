"""
阿里无影云手机执行环境（WuyingMobileEnv）。

职责：把 click/scroll/paste/... 等原子动作映射到 mobile.tap / swipe / input_text / send_key，
截屏 → base64。Session 生命周期由注入的 SessionManager 管理。
"""

from __future__ import annotations

import base64
import inspect
import time
from io import BytesIO
from typing import TYPE_CHECKING, Any

from PIL import Image

import config
import utils.logger as logger
from tools.wuying_cloud.keymap import (
    DBLCLICK_INTERVAL_MS,
    DRAG_DURATION_MS,
    HOTKEY_TO_ANDROID,
    SCROLL_DURATION_MS,
    SCROLL_MAX_PIXELS,
    SCROLL_PIXELS_PER_CLICK,
)
from tools.wuying_cloud.desktop import WuyingDesktopEnv
from tools.wuying_cloud.humanize import (
    jitter_xy,
    maybe_post_pause,
    maybe_pre_pause,
    type_chunked,
)
from tools.wuying_cloud.session import (
    SessionManager,
    SessionState,
    cleanup_orphan_sessions,
    is_session_dead_error,
    platform_from_image_id,
)
from tools.environment import coerce_param
from tools.image_postprocess import encode_for_llm

if TYPE_CHECKING:
    pass


def _call_screenshot(mobile: Any) -> Any:
    """兼容新老 SDK：老版接受 format=，新版 (0.15+) 无参硬编码 PNG。

    统一向老版 SDK 请求 PNG（无损），把 LLM-facing 编码交给 image_postprocess。
    """
    try:
        sig = inspect.signature(mobile.beta_take_screenshot)
    except (TypeError, ValueError):
        sig = None
    if sig is not None and "format" in sig.parameters:
        return mobile.beta_take_screenshot(format="png")
    return mobile.beta_take_screenshot()


class WuyingMobileEnv:
    """阿里无影云手机执行环境。Session 由 SessionManager 懒加载管理。"""

    _ACTION_HANDLERS: dict[str, str] = {
        "click": "_do_tap",
        "dblclick": "_do_double_tap",
        "scroll": "_do_scroll",
        "drag": "_do_drag",
        "paste": "_do_paste",
        "wait": "_do_wait",
        "hotkey": "_do_hotkey",
        "long_press": "_do_long_press",
        "clear_input": "_do_clear_input",
    }
    _NOOP_ACTIONS: dict[str, str] = {
        "move": "云手机无光标，move 已忽略",
        "copy": "云手机无系统剪贴板，copy 已忽略",
    }

    def __init__(
        self,
        session_mgr: SessionManager,
        mode: str = "agentbay",
    ):
        self._session_mgr = session_mgr
        self._mode = mode
        # 输出格式完全由 config.settings.system.screenshot.wuyingcloud.format 决定
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
            result = _call_screenshot(session.mobile)
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

        # SDK 返回原始 PNG bytes → 解码 → 按 wuyingcloud profile 重编码（统一压缩管线）
        profile = config.settings.system.screenshot.wuyingcloud
        img = Image.open(BytesIO(result.data))
        data, content_type = encode_for_llm(img, profile)
        self.content_type = content_type

        b64 = base64.b64encode(data).decode("utf-8")
        w, h = self._session_mgr.screen_size
        logger.debug(
            {
                "msg": f"[{self._mode}] 截图成功",
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
        # 拟人化：可选坐标抖动；config.system.input.tap_jitter_px=0 时等价旧行为
        jx, jy = jitter_xy(x, y, config.settings.system.input.tap_jitter_px)
        maybe_pre_pause()
        result = mobile.tap(jx, jy)
        maybe_post_pause()
        return self._wrap(result, f"已点击 ({jx}, {jy})", finish)

    def _do_double_tap(self, mobile, params: dict, finish: bool) -> dict:
        x, y = self._llm_to_pixel(params)
        jx, jy = jitter_xy(x, y, config.settings.system.input.tap_jitter_px)
        maybe_pre_pause()
        r1 = mobile.tap(jx, jy)
        if not r1.success:
            return self._wrap(r1, f"双击第一次失败 ({jx}, {jy})", finish)
        time.sleep(DBLCLICK_INTERVAL_MS / 1000.0)
        r2 = mobile.tap(jx, jy)
        maybe_post_pause()
        return self._wrap(r2, f"已双击 ({jx}, {jy})", finish)

    def _do_scroll(self, mobile, params: dict, finish: bool) -> dict:
        clicks = int(coerce_param(params, "clicks", 1))
        sx, sy = self._scroll_anchor(params)
        distance = min(abs(clicks) * SCROLL_PIXELS_PER_CLICK, SCROLL_MAX_PIXELS)
        ey = sy + distance if clicks > 0 else sy - distance
        _, sh = self._session_mgr.screen_size
        ey = max(0, min(ey, max(sh - 1, 0)))
        maybe_pre_pause()
        result = mobile.swipe(sx, sy, sx, ey, duration_ms=SCROLL_DURATION_MS)
        maybe_post_pause()
        direction = "上" if clicks > 0 else "下"
        return self._wrap(result, f"滑动({direction} 强度{abs(clicks)})", finish)

    def _do_drag(self, mobile, params: dict, finish: bool) -> dict:
        x1, y1 = self._llm_to_pixel({"x": coerce_param(params, "x1"), "y": coerce_param(params, "y1")})
        x2, y2 = self._llm_to_pixel({"x": coerce_param(params, "x2"), "y": coerce_param(params, "y2")})
        maybe_pre_pause()
        result = mobile.swipe(x1, y1, x2, y2, duration_ms=DRAG_DURATION_MS)
        maybe_post_pause()
        return self._wrap(result, f"已拖拽 ({x1},{y1})→({x2},{y2})", finish)

    def _do_paste(self, mobile, params: dict, finish: bool) -> dict:
        text = str(coerce_param(params, "text", ""))
        if not text:
            return {"ok": True, "message": "paste 文本为空，已跳过", "finish": finish}
        in_cfg = config.settings.system.input
        preview = text[:20] + "…" if len(text) > 20 else text
        maybe_pre_pause()

        # 拟人化：逐字符 input_text + 随机 [min,max]ms 抖动；默认关
        if in_cfg.paste_per_char:
            ok, err, sent = type_chunked(
                mobile.input_text, text,
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
            # 把"已输入到第 N 个字"信息透传，方便 LLM 决定要不要重试或 clear 重来
            err_low = err.lower()
            if "focused editable" in err_low or "no focused" in err_low:
                return {
                    "ok": False,
                    "finish": finish,
                    "message": (
                        f"逐字输入第 {sent + 1}/{len(text)} 字符失败：当前没有**聚焦的可编辑输入框**。"
                        "必须先 `click(x, y)` 点击目标输入框让它获焦、屏幕键盘弹出之后，下一拍再 paste。"
                        f"底层报错：{err}"
                    ),
                }
            return {
                "ok": False,
                "finish": finish,
                "message": f"逐字输入第 {sent + 1}/{len(text)} 字符失败：{err}",
            }

        result = mobile.input_text(text)
        maybe_post_pause()
        if not result.success:
            raw = str(getattr(result, "error_message", "") or "")
            # 云手机最常见的 paste 失败：没有 EditText 获焦；LLM 多半跳了"先 click 输入框"那步
            if "focused editable" in raw.lower() or "no focused" in raw.lower():
                return {
                    "ok": False,
                    "finish": finish,
                    "message": (
                        "paste 失败：当前没有**聚焦的可编辑输入框**（No focused editable node）。"
                        "必须先 `click(x, y)` 点击目标输入框让它获焦、屏幕键盘弹出之后，下一拍再 paste。"
                        "若刚 click 过仍报此错，说明点击坐标没落在真正的输入框上"
                        "（常见：点到了 placeholder 的父容器、搜索栏外壳、图标而不是输入区）；"
                        f"底层报错：{raw}"
                    ),
                }
        return self._wrap(result, f'已输入 "{preview}"', finish)

    @staticmethod
    def _do_wait(params: dict, finish: bool) -> dict:
        ms = float(coerce_param(params, "milliseconds", 0))
        time.sleep(max(ms, 0) / 1000.0)
        return {"ok": True, "message": f"已等待 {ms:.0f}ms", "finish": finish}

    def _do_hotkey(self, mobile, params: dict, finish: bool) -> dict:
        keys = str(coerce_param(params, "keys", "")).strip()
        if "+" in keys:
            return {
                "ok": False,
                "finish": finish,
                "message": (
                    f"云手机不支持组合键 {keys!r}（Android SDK 无 meta 键输入通道）。"
                    "替代方案：清空输入框用 `clear_input(x,y)`；复制粘贴用 `paste(text)`（Android 的 "
                    "input_text 通常会替换当前 EditText 内容）；导航返回用 `hotkey(\"back\")`。"
                ),
            }
        code = HOTKEY_TO_ANDROID.get(keys.lower())
        if code is None:
            return {
                "ok": False,
                "finish": finish,
                "message": (
                    f"云手机未映射的按键 {keys!r}。SDK 仅支持 "
                    "back/esc/home/menu/volume_up/volume_down/power 这 7 种硬件键；"
                    "屏幕键盘上的 Enter/空格/退格/方向键请用**视觉 `click`** 点对应按钮。"
                ),
            }
        maybe_pre_pause()
        result = mobile.send_key(code)
        maybe_post_pause()
        return self._wrap(result, f"已发送按键 {keys}", finish)

    def _do_long_press(self, mobile, params: dict, finish: bool) -> dict:
        """长按。Android 下用 swipe 起止同点 + 长 duration 模拟。
        常见用途：唤起文本选择菜单（全选/复制/粘贴）、图标长按菜单、拖拽前抓取。"""
        x, y = self._llm_to_pixel(params)
        jx, jy = jitter_xy(x, y, config.settings.system.input.tap_jitter_px)
        ms = int(float(coerce_param(params, "milliseconds", 600) or 600))
        ms = max(ms, 400)  # 太短不触发 long-press 语义
        maybe_pre_pause()
        result = mobile.swipe(jx, jy, jx, jy, duration_ms=ms)
        maybe_post_pause()
        return self._wrap(result, f"已在 ({jx}, {jy}) 长按 {ms}ms", finish)

    def _do_clear_input(self, mobile, params: dict, finish: bool) -> dict:
        """清空输入框。移动端语义：tap 获取焦点 + 长按唤起选择菜单；清空的最后一步
        ('全选' → '删除') 需要 LLM 视觉接 —— 本动作诚实地把"菜单已弹出"告诉上层。"""
        x, y = self._llm_to_pixel(params)
        # 先点一下获焦，再长按 600ms
        tap_result = mobile.tap(x, y)
        if not tap_result.success:
            return {"ok": False, "finish": finish,
                    "message": f"clear_input 第一步 tap 失败：{tap_result.error_message}"}
        time.sleep(0.25)
        swipe_result = mobile.swipe(x, y, x, y, duration_ms=650)
        if not swipe_result.success:
            return {"ok": False, "finish": finish,
                    "message": f"clear_input 长按失败：{swipe_result.error_message}"}
        return {
            "ok": True,
            "finish": finish,
            "message": (
                f"已在 ({x}, {y}) 发起清空输入：tap 获焦 + 长按 650ms。"
                "下一拍请从截图确认 Android 选择菜单已弹出（通常显示'全选' / '剪切' / '复制'），"
                "然后 `click` 菜单中的 '全选'，再 `hotkey(\"back\")` 或视觉点键盘上的退格以删除。"
            ),
        }

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
        msg = success_message if ok else (getattr(sdk_result, "error_message", "") or "云手机 SDK 调用失败")
        return {"ok": ok, "message": msg, "finish": finish}


__all__ = [
    "WuyingMobileEnv",
    "WuyingDesktopEnv",
    "SessionManager",
    "SessionState",
    "is_session_dead_error",
    "cleanup_orphan_sessions",
    "platform_from_image_id",
]
