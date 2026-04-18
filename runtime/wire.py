"""
运行时工厂：按 mode 组装 env / session / strategy / worker。

这些原来散在 runtime/worker.py 里以 build_* 开头的函数，现在集中到一个
模块。都是纯函数，没有实例状态——worker.py / host.py 只需 import 使用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import config
from tools.cloud_mobile import CloudMobileEnv, SessionManager
from tools.environment import Environment
from tools.macos_chrome import MacOSChromeEnv

if TYPE_CHECKING:
    from agents.operator import SubtaskStrategy, VisionActionStep
    from runtime.events import EventBus


def _is_remote() -> bool:
    return bool(config.settings.agent.runtime.session_service_url)


def build_session(
    mode: str,
    account_cfg: dict | None,
    events: "EventBus",
    account_id: str,
) -> SessionManager | None:
    """云端模式构造 SessionManager；本地或 remote 模式返回 None。"""
    if mode == "local_chrome" or _is_remote():
        return None
    ab = config.settings.agent.runtime.agentbay
    cfg = account_cfg or {}
    return SessionManager(
        api_key=ab.api_key,
        image_id=cfg.get("image_id") or ab.image_id,
        idle_release_timeout=ab.idle_release_timeout,
        mode=mode,
        event_bus=events,
        account_id=account_id,
    )


def build_env(
    mode: str,
    account_cfg: dict | None = None,
    session_mgr: SessionManager | None = None,
) -> Environment:
    rt = config.settings.agent.runtime
    session_url = rt.session_service_url

    if session_url and mode in ("cloudmobile", "agentbay"):
        from tools.remote import RemoteEnv

        return RemoteEnv(
            session_service_url=session_url,
            account_id=(account_cfg or {}).get("id") or "default",
            api_key=rt.session_service_api_key,
            mode=mode,
        )

    match mode:
        case "local_chrome":
            return MacOSChromeEnv()
        case "cloudmobile" | "agentbay":
            if session_mgr is None:
                raise RuntimeError("云端模式必须提供 SessionManager")
            ab = rt.agentbay
            cfg = account_cfg or {}
            return CloudMobileEnv(
                session_mgr=session_mgr,
                screenshot_format=cfg.get("screenshot_format") or ab.screenshot_format,
                mode=mode,
            )
        case _:
            raise RuntimeError(f"未知 mode={mode!r}")


def build_strategy(
    mode: str,
    *,
    vision_step: "VisionActionStep",
    session_mgr: SessionManager | None = None,
    account_cfg: dict | None = None,
) -> "SubtaskStrategy":
    from agents.operator import AgentBayDelegateStrategy

    rt = config.settings.agent.runtime
    session_url = rt.session_service_url

    match mode:
        case "local_chrome" | "cloudmobile":
            return vision_step
        case "agentbay":
            if session_url:
                from tools.remote import RemoteDelegateStrategy

                return RemoteDelegateStrategy(
                    session_service_url=session_url,
                    account_id=(account_cfg or {}).get("id") or "default",
                    api_key=rt.session_service_api_key,
                )
            if session_mgr is None:
                raise RuntimeError("agentbay 模式必须提供 SessionManager")
            return AgentBayDelegateStrategy(session_mgr=session_mgr)
        case _:
            raise RuntimeError(f"未知 mode={mode!r}")


def close_env(env: Environment) -> None:
    """关闭 env 持有的资源（仅 RemoteEnv 有 httpx.Client）。"""
    close = getattr(env, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def state_file_for(account_cfg: dict | None) -> str:
    """决定 state 文件路径。"""
    if account_cfg:
        if account_cfg.get("state_file"):
            return account_cfg["state_file"]
        return f"data/accounts/{account_cfg['id']}/agent_state.json"
    return config.settings.agent.storage.state_file


__all__ = [
    "build_env",
    "build_session",
    "build_strategy",
    "close_env",
    "state_file_for",
]
