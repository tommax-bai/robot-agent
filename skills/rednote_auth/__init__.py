"""rednote.auth: 小红书登录与状态检查。"""

from __future__ import annotations

import random
import time

import utils.logger as logger
from bootstrap.chrome_client import get as get_chrome
from services.waiting_queue import default as waiting_queue
from skills import Pack, ToolContext, ToolOutcome

pack = Pack(
    name="rednote.auth",
    description="小红书账户登录与状态检查模块。用于打开应用、通过视觉判断登录状态并执行登录。",
    supports=("local_chrome",),
)

_VERIFICATION_TIMEOUT_SECONDS = 180
_VERIFICATION_POLL_INTERVAL = 1


@pack.tool
def open_rednote_homepage(ctx: ToolContext) -> ToolOutcome:
    """打开小红书首页。已有小红书标签页时切换，否则新开。"""
    chrome = get_chrome()
    for tab in chrome.list_tabs():
        if "小红书" in tab.get("title", "") or "xiaohongshu.com" in tab.get("url", ""):
            logger.info({"msg": "切换到现有小红书标签页", "tab_id": tab["id"]}, ctx.trace_id)
            chrome.switch_to_tab(tab["id"])
            return ToolOutcome.success("已切换到现有的小红书标签页")

    target_url = "https://www.xiaohongshu.com/explore"
    logger.info({"msg": "新开小红书标签页", "url": target_url}, ctx.trace_id)
    new_tab_id = chrome.new_tab(target_url)
    if new_tab_id:
        chrome.switch_to_tab(new_tab_id)
    time.sleep(random.uniform(4, 6))
    return ToolOutcome.success("已成功打开并切换到小红书首页")


@pack.tool
def send_verification_code(ctx: ToolContext) -> ToolOutcome:
    """等待验证码回调。阻塞式等待，带超时保护。"""
    waiting_queue.add(ctx.trace_id, "send_verification_code")
    deadline = time.time() + _VERIFICATION_TIMEOUT_SECONDS
    try:
        while time.time() < deadline:
            entry = waiting_queue.get(ctx.trace_id)
            if entry and entry["completed"]:
                logger.info({"msg": "收到验证码", "trace_id": ctx.trace_id})
                return ToolOutcome.success(f"小红书验证码为: {entry['result']}")
            time.sleep(_VERIFICATION_POLL_INTERVAL)
        logger.error({
            "msg": "等待验证码超时",
            "trace_id": ctx.trace_id,
            "timeout_seconds": _VERIFICATION_TIMEOUT_SECONDS,
        })
        return ToolOutcome.failure(f"等待验证码超时（{_VERIFICATION_TIMEOUT_SECONDS}秒）")
    finally:
        waiting_queue.remove(ctx.trace_id)
