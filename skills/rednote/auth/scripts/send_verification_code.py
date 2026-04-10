"""
等待验证码回调：阻塞式等待，带超时保护防止永久卡死。
"""
import time

import utils.logger as logger
import utils.waiting_queue as waiting_queue

# 验证码等待超时（秒）。3 分钟后仍未到达视为失败。
_TIMEOUT_SECONDS = 180
_POLL_INTERVAL = 1


def send_verification_code(trace_id: str):
    waiting_queue.add_queue(trace_id, "send_verification_code")
    deadline = time.time() + _TIMEOUT_SECONDS

    try:
        while time.time() < deadline:
            entry = waiting_queue.get_queue(trace_id)
            if entry and entry["completed"]:
                logger.info({"msg": "收到验证码", "trace_id": trace_id})
                return {
                    "ok": True,
                    "message": f"小红书验证码为: {entry['result']}",
                    "finish": False,
                }
            time.sleep(_POLL_INTERVAL)

        logger.error({
            "msg": "等待验证码超时",
            "trace_id": trace_id,
            "timeout_seconds": _TIMEOUT_SECONDS,
        })
        return {
            "ok": False,
            "message": f"等待验证码超时（{_TIMEOUT_SECONDS}秒）",
            "finish": False,
        }
    finally:
        waiting_queue.remove_queue(trace_id)
