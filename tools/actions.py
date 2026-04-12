from __future__ import annotations

from tools.screen import llm_to_screen
import pyautogui
import pyperclip
import config
import time
import random
import math

# 禁用 PyAutoGUI 的故障保护（FAILSAFE），防止在 macOS 上因鼠标在角落而导致任务中断。
pyautogui.FAILSAFE = False


# ═══════════════════════════════════════════════════════
# 拟人化基础工具
# ═══════════════════════════════════════════════════════

def _human_move(x, y):
    """
    拟人化鼠标移动：贝塞尔曲线轨迹 + 速度曲线。
    短距离快移，长距离分段加速减速。
    """
    cx, cy = pyautogui.position()
    dist = math.hypot(x - cx, y - cy)

    if dist < 5:
        pyautogui.moveTo(x, y)
        return

    # 根据距离动态计算移动时长：短距离快、长距离适中
    duration = min(0.15 + dist / 3000, 0.5)

    # 生成一个随机的贝塞尔控制点，让轨迹微微弯曲
    mid_x = (cx + x) / 2 + random.uniform(-dist * 0.05, dist * 0.05)
    mid_y = (cy + y) / 2 + random.uniform(-dist * 0.05, dist * 0.05)

    steps = max(int(dist / 75), 6)
    for i in range(1, steps + 1):
        t = i / steps
        # 缓入缓出：ease-in-out
        t_ease = t * t * (3 - 2 * t)
        # 二次贝塞尔曲线
        bx = (1 - t_ease) ** 2 * cx + 2 * (1 - t_ease) * t_ease * mid_x + t_ease ** 2 * x
        by = (1 - t_ease) ** 2 * cy + 2 * (1 - t_ease) * t_ease * mid_y + t_ease ** 2 * y
        pyautogui.moveTo(bx, by)
        time.sleep(duration / steps)

    # 最终精确归位
    pyautogui.moveTo(x, y)


def _jitter_offset(radius=2):
    """生成微小的随机偏移，模拟人手不可能完美命中像素中心"""
    return random.randint(-radius, radius), random.randint(-radius, radius)


def _pre_action_pause():
    """动作前的微停顿：人在点击前会有极短的犹豫"""
    time.sleep(random.uniform(0.02, 0.08))


def _post_action_pause():
    """动作后的微停顿：人在点击后会短暂观察结果"""
    time.sleep(random.uniform(0.05, 0.15))


# ═══════════════════════════════════════════════════════
# 动作执行入口
# ═══════════════════════════════════════════════════════

def execute_action(trace_id: str, data: dict) -> dict:
    from utils import logger
    import json

    method = data.get("method", None)
    params = data.get("params", {})

    logger.info({
        "msg": "执行器收到动作指令",
        "method": method,
        "params_raw": json.dumps(params, ensure_ascii=False)
    }, trace_id)

    if not method:
        return {
            "ok": False,
            "message": "没有拿到有效的 action",
            "error": "missing action field",
            "finish": False
        }

    finish = bool(data.get("finish", False))

    try:
        match method:
            case "click":
                return _human_click(params, finish, trace_id)
            case "dblclick":
                return _human_dblclick(params, finish, trace_id)
            case "move":
                return _human_move_action(params, finish, trace_id)
            case "scroll":
                return _human_scroll(params, finish, trace_id)
            case "drag":
                return _human_drag_action(params, finish, trace_id)
            case "paste":
                return _human_paste(params, finish, trace_id)
            case "copy":
                return _human_copy(params, finish, trace_id)
            case "wait":
                return _human_wait(params, finish, trace_id)
            case "hotkey":
                return _human_hotkey(params, finish, trace_id)
            case _:
                return {"ok": False, "message": f"不支持的动作: {method}", "error": "unsupported action", "finish": finish}

    except Exception as e:
        logger.error({"msg": f"执行器内部报错: {method}", "error": str(e), "params": params}, trace_id)
        return {"ok": False, "message": f"执行报错: {str(e)}", "error": type(e).__name__, "finish": finish}


# ═══════════════════════════════════════════════════════
# 各动作的拟人化实现
# ═══════════════════════════════════════════════════════

def _human_click(params, finish, trace_id):
    """拟人化单击：曲线移动 → 微停顿 → 点击（带微偏移） → 短暂观察"""
    from utils import logger

    x_val = get_param(params, "x")
    y_val = get_param(params, "y")
    if x_val is None or y_val is None:
        raise KeyError(f"缺少坐标参数 x 或 y, 收到: {list(params.keys())}")

    x, y = llm_to_screen(float(x_val), float(y_val))
    jx, jy = _jitter_offset(2)

    logger.info(f"human click → ({x}, {y})")
    _human_move(x + jx, y + jy)
    _pre_action_pause()
    pyautogui.click()
    _post_action_pause()

    return {"ok": True, "message": f"点击操作已执行 ({x}, {y})", "finish": finish}


def _human_dblclick(params, finish, trace_id):
    """拟人化双击：曲线移动 → 微停顿 → 双击（间隔随机化） → 短暂观察"""
    x_val = get_param(params, "x")
    y_val = get_param(params, "y")
    x, y = llm_to_screen(float(x_val), float(y_val))
    jx, jy = _jitter_offset(2)

    _human_move(x + jx, y + jy)
    _pre_action_pause()
    # 双击间隔人类通常在 50-120ms
    interval = random.uniform(0.05, 0.12)
    pyautogui.click()
    time.sleep(interval)
    pyautogui.click()
    _post_action_pause()

    return {"ok": True, "message": f"双击操作已执行 ({x}, {y})", "finish": finish}


def _human_move_action(params, finish, trace_id):
    """拟人化移动：贝塞尔曲线到达目标位置"""
    from utils import logger

    x_val = get_param(params, "x")
    y_val = get_param(params, "y")
    x, y = llm_to_screen(float(x_val), float(y_val))

    logger.info(f"human move → ({x}, {y})")
    _human_move(x, y)
    # 移到目标后短暂悬停，模拟人在看悬浮提示
    time.sleep(random.uniform(0.1, 0.3))

    return {"ok": True, "message": f"移动操作已执行 ({x}, {y})", "finish": finish}


def _human_scroll(params, finish, trace_id):
    """拟人化滚动：先移到目标区域 → 分段滚动，步长/节奏随机"""
    clicks = int(get_param(params, "clicks", 1))
    x_val = get_param(params, "x")
    y_val = get_param(params, "y")

    if x_val is not None and y_val is not None:
        sx, sy = llm_to_screen(float(x_val), float(y_val))
        _human_move(sx, sy)
        time.sleep(random.uniform(0.05, 0.15))
    else:
        x1 = get_param(params, "x1")
        y1 = get_param(params, "y1")
        if x1 is None or y1 is None:
            raise KeyError(f"缺少滚动坐标参数, 收到: {list(params.keys())}")
        sx, sy = llm_to_screen(float(x1), float(y1))
        _human_move(sx, sy)
        time.sleep(random.uniform(0.05, 0.15))

    # 物理缩减系数，让 LLM 指令与实际滚动量之间留有余地
    abs_clicks = int(abs(clicks) * 0.5)
    direction = 1 if clicks > 0 else -1
    abs_clicks = min(abs_clicks, 200)

    remaining = abs_clicks
    while remaining > 0:
        # 模拟人类小幅度拨动滚轮，步长随机
        step = min(random.randint(30, 50), remaining)
        pyautogui.scroll(step * direction)
        remaining -= step

        # 模拟边滚边看的节奏：偶尔停下来多看一会
        if random.random() < 0.15:
            time.sleep(random.uniform(0.3, 0.6))
        elif random.random() < 0.4:
            time.sleep(random.uniform(0.1, 0.25))
        else:
            time.sleep(random.uniform(0.04, 0.1))

    return {"ok": True, "message": f"滚动操作已执行 (方向:{'上' if clicks > 0 else '下'}, 强度:{abs(clicks)})", "finish": finish}


def _human_drag_action(params, finish, trace_id):
    """拟人化拖拽：三段式加速度（慢启动 → 冲刺 → 精准减速）"""
    x1 = float(get_param(params, "x1"))
    y1 = float(get_param(params, "y1"))
    x2 = float(get_param(params, "x2"))
    y2 = float(get_param(params, "y2"))
    sx1, sy1 = llm_to_screen(x1, y1)
    sx2, sy2 = llm_to_screen(x2, y2)
    _human_drag(sx1, sy1, sx2, sy2)
    return {"ok": True, "message": "拖动操作已执行", "finish": finish}


def _human_paste(params, finish, trace_id):
    """拟人化粘贴：先短暂停顿（像在确认光标位置） → 粘贴 → 短暂观察"""
    text = get_param(params, "text", "")
    pyperclip.copy(text)

    _pre_action_pause()
    platform = config.system["system_info"]
    modifier = "command" if platform == "darwin" else "ctrl"
    pyautogui.hotkey(modifier, 'v')
    # 粘贴后等内容渲染
    time.sleep(random.uniform(0.1, 0.3))

    return {"ok": True, "message": "粘贴操作已执行", "finish": finish}


def _human_copy(params, finish, trace_id):
    """拟人化复制：与粘贴对称，添加微延迟"""
    text = get_param(params, "text", "")
    pyperclip.copy(text)
    time.sleep(random.uniform(0.03, 0.08))
    return {"ok": True, "message": "复制操作已执行", "finish": finish}


def _human_wait(params, finish, trace_id):
    """拟人化等待：在指定时长基础上 ±10% 抖动"""
    ms = float(get_param(params, "milliseconds", 0))
    jitter = ms * random.uniform(-0.1, 0.1)
    actual_ms = max(ms + jitter, 0)
    time.sleep(actual_ms / 1000.0)
    return {"ok": True, "message": f"等待操作已执行 ({actual_ms:.0f}ms)", "finish": finish}


def _human_hotkey(params, finish, trace_id):
    """拟人化快捷键：按键间隔随机化，模拟手指依次按下"""
    keys = get_param(params, "keys", "")
    key_list = keys.split("+")

    _pre_action_pause()
    # 依次按下
    for key in key_list:
        pyautogui.keyDown(key.strip())
        time.sleep(random.uniform(0.02, 0.06))
    # 短暂保持
    time.sleep(random.uniform(0.03, 0.08))
    # 依次松开（反序）
    for key in reversed(key_list):
        pyautogui.keyUp(key.strip())
        time.sleep(random.uniform(0.01, 0.04))
    _post_action_pause()

    return {"ok": True, "message": "快捷键操作已执行", "finish": finish}


# ═══════════════════════════════════════════════════════
# 参数获取 & 拖拽底层实现
# ═══════════════════════════════════════════════════════

def get_param(params: dict, key: str, default=None):
    """
    极其强悍的参数获取助手，能处理各种 LLM 返回的脏数据键名
    """
    if not isinstance(params, dict):
        return default

    if key in params:
        return params[key]

    variants = [
        key.lower(),
        key.upper(),
        f'"{key}"',
        f"'{key}'",
        f'\\"{key}\\"',
        f' {key} ',
    ]
    for v in variants:
        if v in params:
            return params[v]

    for k in params.keys():
        clean_k = str(k).strip().strip('"').strip("'").strip('\\"').lower()
        if clean_k == key.lower():
            return params[k]

    return default


def _human_drag(x1, y1, x2, y2):
    """
    三段式拟人化拖拽：
    阶段 1 (0-15%): 慢启动，较多延迟
    阶段 2 (15-85%): 快速冲刺，极低延迟
    阶段 3 (85-100%): 精准减速，抖动淡出
    """
    dist_x = x2 - x1
    dist_y = y2 - y1

    # 1. 曲线移动到起点
    _human_move(x1, y1)
    time.sleep(random.uniform(0.1, 0.2))
    pyautogui.mouseDown()

    def get_jitter():
        return random.randint(-1, 0)

    # 阶段 1: 慢启动 (0% - 15%)
    for p in range(0, 15, 1):
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio) + get_jitter()
        cur_y = y1 + (dist_y * ratio) + get_jitter()
        cur_x = min(cur_x, x1 + dist_x * 0.15)
        pyautogui.moveTo(cur_x, cur_y, duration=0.001)
        if random.random() < 0.4:
            time.sleep(0.002)

    # 阶段 2: 快速冲刺 (15% - 85%)
    p = 15
    while p < 85:
        step = random.randint(4, 8)
        step = min(step, 85 - p)
        p += step
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio)
        cur_y = y1 + (dist_y * ratio)
        pyautogui.moveTo(cur_x, cur_y, duration=0.0005)
        if random.random() < 0.05:
            time.sleep(0.001)

    # 阶段 3: 精准减速 (85% - 100%)
    for p in range(85, 101, 1):
        fade_factor = (100 - p) / 15.0
        jitter_y = 0
        if random.random() < fade_factor:
            jitter_y = random.randint(-1, 1)
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio)
        cur_y = y1 + (dist_y * ratio) + jitter_y
        pyautogui.moveTo(cur_x, cur_y, duration=0.001)
        if random.random() < 0.5 * fade_factor:
            time.sleep(0.003)

    # 精确归位并松开
    pyautogui.moveTo(x2, y2, duration=0.1)
    time.sleep(random.uniform(0.1, 0.2))
    pyautogui.mouseUp()
