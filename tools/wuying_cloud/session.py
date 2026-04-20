"""
阿里无影云手机 Session 生命周期状态机。

职责单一：管理 AgentBay session 的创建、使用、死亡检测、主动释放、URL 刷新。
每次状态转换都通过 event_bus 推 SSE 事件，dashboard 秒级感知。

    IDLE ──acquire()──▸ CREATING ──success──▸ ACTIVE
      ▴                                        │
      │                              mark_dead() / release()
      │                                        │
      └────────────────────────────────────────┘
"""

from __future__ import annotations

import enum
import inspect
import threading
import time
from typing import TYPE_CHECKING, Any

import utils.logger as logger

if TYPE_CHECKING:
    from runtime.events import EventBus


# 阿里返回的 "session 已不存在" 错误关键字
_SESSION_DEAD_MARKERS = (
    "InvalidAppInstanceGroup",
    "NotContainThisAppInstance",
    "SessionNotFound",
    "SessionExpired",
    "session not found",
    "session has been released",
    "session has been deleted",
    "InstanceGroup",
)


def is_session_dead_error(e: BaseException) -> bool:
    msg = str(e)
    return any(marker in msg for marker in _SESSION_DEAD_MARKERS)


def _supports_param(cls: type, name: str) -> bool:
    """wuying-agentbay-sdk 0.15 起删掉了 idle_release_timeout；用 introspection 决定是否传。"""
    try:
        return name in inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return False


_IDLE_RELEASE_WARNED = False


def _warn_idle_release_once(value: int) -> None:
    """当前安装的 SDK 不支持 idle_release_timeout，只在进程内告警一次。"""
    global _IDLE_RELEASE_WARNED
    if _IDLE_RELEASE_WARNED:
        return
    _IDLE_RELEASE_WARNED = True
    logger.warning({
        "msg": "当前 wuying-agentbay-sdk 不支持 idle_release_timeout，已忽略；"
               "仅依赖启动时孤儿清理（AGENT_AUTO_CLEANUP_ORPHANS）",
        "requested_seconds": value,
    })


class SessionState(enum.Enum):
    IDLE = "idle"
    CREATING = "creating"
    ACTIVE = "active"


def platform_from_image_id(image_id: str) -> str:
    """根据镜像 ID 判断云端实例类型："mobile" (Android) 或 "desktop" (Windows/Linux)。

    约定：带 'mobile' 关键字 → 移动端；带 'windows'/'linux'/'computer' 关键字 → 桌面；
    其余默认 'mobile'，保留向后兼容（早期仓库只跑 Android 云手机）。"""
    s = (image_id or "").lower()
    if "mobile" in s:
        return "mobile"
    if "windows" in s or "linux" in s or s.startswith("computer"):
        return "desktop"
    return "mobile"


class SessionManager:
    """per-worker 的云手机 session 生命周期管理器。"""

    # authCode TTL ~30min，提前 5min 刷新
    _URL_REFRESH_AFTER_SECONDS = 25 * 60

    def __init__(
        self,
        *,
        api_key: str,
        image_id: str = "mobile_latest",
        idle_release_timeout: int | None = 600,
        mode: str = "agentbay",
        event_bus: EventBus,
        account_id: str,
    ):
        self._api_key = api_key
        self._image_id = image_id
        self._idle_release_timeout = idle_release_timeout
        self._mode = mode
        self._event_bus = event_bus
        self._account_id = account_id

        self._lock = threading.Lock()
        self._state = SessionState.IDLE
        self._client: Any = None
        self._session: Any = None
        self._screen_w: int = 0
        self._screen_h: int = 0
        self._resource_url_ts: float = 0.0
        self._cached_url: str = ""

    # ── 只读属性 ──────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def session_id(self) -> str:
        if self._session is None:
            return ""
        return getattr(self._session, "session_id", "") or ""

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._screen_w, self._screen_h

    @property
    def image_id(self) -> str:
        return self._image_id

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def platform(self) -> str:
        """从 image_id 推断：'mobile' (Android 云手机) / 'desktop' (Windows/Linux 云桌面)。"""
        return platform_from_image_id(self._image_id)

    def update_screen_size(self, w: int, h: int) -> None:
        self._screen_w = w
        self._screen_h = h

    # ── 核心生命周期 ──────────────────────────────────────────

    def acquire(self, trace_id: str) -> Any:
        """获取 session（懒创建，线程安全）。返回 SDK Session 对象。"""
        if self._state == SessionState.ACTIVE and self._session is not None:
            return self._session
        with self._lock:
            if self._state == SessionState.ACTIVE and self._session is not None:
                return self._session
            return self._do_create(trace_id)

    def release(self) -> None:
        """主动释放 session（停计费）。失败只记日志。"""
        with self._lock:
            if self._session is None:
                return
            sid = self.session_id
            try:
                if self._client is not None:
                    result = self._client.delete(self._session)
                    ok = bool(getattr(result, "success", True))
                    logger.info({"msg": f"[{self._mode}] 云手机 session 已释放", "session_id": sid, "ok": ok})
            except Exception as e:
                logger.warning({"msg": f"[{self._mode}] 云手机 session 释放失败（将由服务端 idle 回收）", "error": str(e)})
            self._clear()
            self._publish("session_released", session_id=sid)

    def mark_dead(self, source: str, reason: str = "", trace_id: str = "") -> None:
        """被动检测到 session 已死（云端已不存在），清本地引用。"""
        with self._lock:
            sid = self.session_id or "?"
            logger.warning(
                {
                    "msg": f"[{self._mode}] 云手机 session 失效，已清理本地引用（来自 {source}）",
                    "session_id": sid,
                    "reason": (reason[:160] + "…") if len(reason) > 160 else reason,
                },
                trace_id,
            )
            self._clear()
            self._publish("session_dead", session_id=sid, source=source)

    # ── URL 管理 ──────────────────────────────────────────────

    def get_url(self) -> str:
        """纯缓存读取，不做 API 调用。过期返回空串 → dashboard 显示 placeholder。"""
        if self._session is None:
            return ""
        if not self._cached_url:
            return ""
        elapsed = time.time() - self._resource_url_ts
        if elapsed >= self._URL_REFRESH_AFTER_SECONDS:
            return ""
        return self._cached_url

    def refresh_url(self) -> str:
        """显式刷新串流 URL（调 get_link API），由 /agent/live-url 触发。"""
        if self._session is None:
            return ""
        return self._do_refresh_url(self._cached_url)

    def _do_refresh_url(self, fallback: str) -> str:
        try:
            result = self._session.get_link()
            url = (getattr(result, "data", "") or "") if getattr(result, "success", False) else ""
            if url:
                self._cached_url = url
                self._resource_url_ts = time.time()
                return url
        except Exception as e:
            logger.warning({"msg": f"[{self._mode}] 刷新串流 URL 失败，回退到缓存", "error": str(e)})
        return fallback

    # ── 内部 ──────────────────────────────────────────────────

    def _do_create(self, trace_id: str) -> Any:
        """在持锁状态下创建 session。任何路径失败都要把状态机复位到 IDLE，
        否则 _state 卡在 CREATING，下次 acquire 的 double-check 仍会走到这里但
        整个机器对外呈现不一致（state.value 读到 creating，却没有 session）。"""
        try:
            from agentbay import AgentBay, CreateSessionParams
        except ImportError as e:
            raise RuntimeError("未安装 wuying-agentbay-sdk。请执行 `pip install wuying-agentbay-sdk`") from e

        self._state = SessionState.CREATING
        self._publish("session_creating", trace_id=trace_id)

        logger.info(
            {
                "msg": f"[{self._mode}] 正在创建云手机 session",
                "image_id": self._image_id,
                "idle_release_timeout": self._idle_release_timeout,
            },
            trace_id,
        )

        try:
            client = AgentBay(api_key=self._api_key)
            kwargs: dict[str, Any] = {"image_id": self._image_id}
            if self._idle_release_timeout is not None:
                if _supports_param(CreateSessionParams, "idle_release_timeout"):
                    kwargs["idle_release_timeout"] = self._idle_release_timeout
                else:
                    _warn_idle_release_once(self._idle_release_timeout)
            params = CreateSessionParams(**kwargs)
            result = client.create(params)
            # SessionResult 的结构：{success, error_message, session, request_id}
            # 失败时 session=None；不能 `or result` 退化成 SessionResult 本身，
            # 否则下游访问 `.mobile` / `.agent` 会是 AttributeError，掩盖真实失败原因。
            ok = getattr(result, "success", True)  # 旧版返回可能没 success 字段，兜底 True
            session = getattr(result, "session", None)
            if not ok or session is None:
                err = getattr(result, "error_message", "") or "返回为空"
                req_id = getattr(result, "request_id", "")
                raise RuntimeError(
                    f"[{self._mode}] 云手机 session 创建失败 (image_id={self._image_id}): "
                    f"{err}" + (f" [request_id={req_id}]" if req_id else "")
                )

            self._client = client
            self._session = session
            self._cached_url = getattr(session, "resource_url", "") or ""
            self._resource_url_ts = time.time()
            self._state = SessionState.ACTIVE
        except BaseException:
            self._state = SessionState.IDLE
            self._publish("session_create_failed", trace_id=trace_id)
            raise

        resource_url = getattr(session, "resource_url", "") or ""
        logger.info(
            {
                "msg": f"[{self._mode}] 云手机 session 已就绪",
                "session_id": getattr(session, "session_id", "?"),
                "live_view_url": resource_url,
            },
            trace_id,
        )
        self._publish(
            "session_active",
            trace_id=trace_id,
            session_id=getattr(session, "session_id", ""),
            live_view_url=resource_url,
        )
        return session

    def _clear(self) -> None:
        self._session = None
        self._client = None
        self._screen_w = 0
        self._screen_h = 0
        self._resource_url_ts = 0.0
        self._cached_url = ""
        self._state = SessionState.IDLE

    def _publish(self, event: str, **ctx) -> None:
        from runtime.events import Event, payload
        self._event_bus.publish(
            Event.RUNTIME_CHANGED,
            payload(account=self._account_id, event=event, **ctx),
        )


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


__all__ = [
    "SessionManager",
    "SessionState",
    "is_session_dead_error",
    "cleanup_orphan_sessions",
    "platform_from_image_id",
]
