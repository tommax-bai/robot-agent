from utils.screen import normalize_to_screen
import pyautogui
import pyperclip
import config
import time
import random

# 禁用 PyAutoGUI 的故障保护（FAILSAFE），防止在 macOS 上因鼠标在角落而导致任务中断。
# 注意：这要求代码逻辑必须严密，防止出现死循环点击。
pyautogui.FAILSAFE = False

def do_actions_step(trace_id: str, data: dict) -> dict:
    from utils import logger
    import json
    
    method = data.get("method", None)
    params = data.get("params", {})
    
    # 记录极其详细的入参日志，方便排查
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
                x_val = get_param(params, "x")
                y_val = get_param(params, "y")
                if x_val is None or y_val is None:
                    raise KeyError(f"缺少坐标参数 x 或 y, 收到: {list(params.keys())}")
                x, y = normalize_to_screen(float(x_val), float(y_val))
                pyautogui.click(x=x, y=y)
                return {"ok": True, "message": f"点击成功 ({x}, {y})", "finish": finish}

            case "dblclick":
                x_val = get_param(params, "x")
                y_val = get_param(params, "y")
                x, y = normalize_to_screen(float(x_val), float(y_val))
                pyautogui.doubleClick(x=x, y=y)
                return {"ok": True, "message": f"双击成功 ({x}, {y})", "finish": finish}

            case "move":
                x_val = get_param(params, "x")
                y_val = get_param(params, "y")
                x, y = normalize_to_screen(float(x_val), float(y_val))
                pyautogui.moveTo(x=x, y=y)
                return {"ok": True, "message": f"移动成功 ({x}, {y})", "finish": finish}

            case "scroll":
                clicks = int(get_param(params, "clicks", 1))
                x_val = get_param(params, "x")
                y_val = get_param(params, "y")
                
                if x_val is not None and y_val is not None:
                    sx, sy = normalize_to_screen(float(x_val), float(y_val))
                    # 拟人化修正：点击屏幕边缘的安全区以激活窗口，防止误触
                    # screen_width = config.global_config["screen_size"]["width"]
                    # safe_x = 50 if sx > (screen_width / 2) else (screen_width - 50)
                    # pyautogui.click(x=safe_x, y=sy)
                    # time.sleep(0.1)
                    pyautogui.moveTo(sx, sy)
                    # 2. 柔和滚动优化：缩减物理倍率，拆解为更细碎的步长
                    # 增加 0.5 的物理缩减系数，让 LLM 以为滚了 400，实际物理位移更短
                    abs_clicks = int(abs(clicks) * 0.5)
                    direction = 1 if clicks > 0 else -1
                    
                    # 限制单次执行的最大滚动量，防止“疯狂滚动”
                    max_total_clicks = 800
                    if abs_clicks > max_total_clicks:
                        abs_clicks = max_total_clicks
                    
                    remaining = abs_clicks
                    while remaining > 0:
                        # 步长更细碎（20-60），模拟人类小幅度拨动滚轮
                        step = min(random.randint(20, 60), remaining)
                        pyautogui.scroll(step * direction)
                        remaining -= step
                        
                        # 增加停顿频率，模拟人类边滚边看的节奏
                        if random.random() < 0.4:
                            time.sleep(random.uniform(0.15, 0.4))
                        else:
                            time.sleep(0.08)
                else:
                    x1 = get_param(params, "x1")
                    y1 = get_param(params, "y1")
                    if x1 is None or y1 is None:
                        raise KeyError(f"缺少滚动坐标参数, 收到: {list(params.keys())}")
                    sx1, sy1 = normalize_to_screen(float(x1), float(y1))
                    # 同样点击安全边缘
                    # screen_width = config.global_config["screen_size"]["width"]
                    # safe_x1 = 50 if sx1 > (screen_width / 2) else (screen_width - 50)
                    # pyautogui.click(x=safe_x1, y=sy1)
                    # time.sleep(0.1)
                    pyautogui.moveTo(sx1, sy1)
                    pyautogui.scroll(clicks)
                
                return {"ok": True, "message": f"滚动成功 (方向:{'上' if clicks > 0 else '下'}, 强度:{abs(clicks)})", "finish": finish}

            case "drag":
                x1 = float(get_param(params, "x1"))
                y1 = float(get_param(params, "y1"))
                x2 = float(get_param(params, "x2"))
                y2 = float(get_param(params, "y2"))
                sx1, sy1 = normalize_to_screen(x1, y1)
                sx2, sy2 = normalize_to_screen(x2, y2)
                human_drag(sx1, sy1, sx2, sy2)
                return {"ok": True, "message": "拖动成功", "finish": finish}

            case "paste":
                text = get_param(params, "text", "")
                pyperclip.copy(text)
                platform = config.global_config["system_info"]
                modifier = "command" if platform == "darwin" else "ctrl"
                pyautogui.hotkey(modifier, 'v')
                return {"ok": True, "message": "粘贴成功", "finish": finish}

            case "copy":
                text = get_param(params, "text", "")
                pyperclip.copy(text)
                return {"ok": True, "message": "复制成功", "finish": finish}

            case "wait":
                ms = get_param(params, "milliseconds", 0)
                time.sleep(float(ms) / 1000.0)
                return {"ok": True, "message": "等待成功", "finish": finish}

            case "hotkey":
                keys = get_param(params, "keys", "")
                pyautogui.hotkey(*keys.split("+"))
                return {"ok": True, "message": "快捷键成功", "finish": finish}

            case _:
                return {"ok": False, "message": f"不支持的动作: {method}", "error": "unsupported action", "finish": finish}
                
    except Exception as e:
        logger.error({"msg": f"执行器内部报错: {method}", "error": str(e), "params": params}, trace_id)
        return {"ok": False, "message": f"执行报错: {str(e)}", "error": type(e).__name__, "finish": finish}

def get_param(params: dict, key: str, default=None):
    """
    极其强悍的参数获取助手，能处理各种 LLM 返回的脏数据键名
    """
    if not isinstance(params, dict):
        return default
    
    # 1. 直接尝试
    if key in params:
        return params[key]
    
    # 2. 尝试各种变体
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
            
    # 3. 最后的挣扎：遍历所有键名，看哪个长得像
    for k in params.keys():
        clean_k = str(k).strip().strip('"').strip("'").strip('\\"').lower()
        if clean_k == key.lower():
            return params[k]
            
    return default

def human_drag(x1, y1, x2, y2):
    # 计算总位移（改用浮点型，避免整数截断）
    dist_x = x2 - x1
    dist_y = y2 - y1
    
    # 1. 移动到起点并准备（增加微小duration，模拟人类移动鼠标的平滑感）
    pyautogui.moveTo(x1, y1, duration=0.2)
    time.sleep(0.15)
    pyautogui.mouseDown()
    
    # 定义抖动函数（限制x轴抖动范围，避免累积偏右）
    def get_jitter():
        # 改为-1到0的轻微负向偏移，抵消随机正向偏差
        return random.randint(-1, 0)

    # 阶段 1: 慢启动 (0% - 15%)
    # 步长 1%, 较多延迟，改用浮点计算
    for p in range(0, 15, 1):
        # 浮点计算位移比例，避免整数截断
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio) + get_jitter()
        cur_y = y1 + (dist_y * ratio) + get_jitter()
        # 强制cur_x不超过x1 + dist_x*0.15，避免提前超界
        cur_x = min(cur_x, x1 + dist_x * 0.15)
        pyautogui.moveTo(cur_x, cur_y, duration=0.001)  # 微小duration平滑移动
        if random.random() < 0.4:  # 40% 概率延迟
            time.sleep(0.002)

    # 阶段 2: 快速冲刺 (15% - 85%)
    # 优化步长逻辑，避免超界后突跳
    p = 15
    while p < 85:
        step = random.randint(4, 8)
        # 计算本次步长的最大安全值，避免超界
        max_step = 85 - p
        step = min(step, max_step)
        p += step
        
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio)
        cur_y = y1 + (dist_y * ratio)
        pyautogui.moveTo(cur_x, cur_y, duration=0.0005)  # 更快但平滑
        
        if random.random() < 0.05: # 5% 模拟手抖微顿
            time.sleep(0.001)

    # 阶段 3: 精准减速与抖动淡出 (85% - 100%)
    # 步长 1%, 淡出因子优化，避免x轴正向抖动
    for p in range(85, 101, 1):
        # 计算淡出因子: 从 1.0 (85%) 降低到 0.0 (100%)
        fade_factor = (100 - p) / 15.0
        
        jitter_x = 0
        jitter_y = 0
        # 抖动仅在y轴保留，x轴禁用（彻底避免偏右）
        if random.random() < fade_factor:
            jitter_y = random.randint(-1, 1)

        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio) + jitter_x
        cur_y = y1 + (dist_y * ratio) + jitter_y
        
        pyautogui.moveTo(cur_x, cur_y, duration=0.001)
        
        # 延迟随淡出因子调整，模拟微调
        if random.random() < 0.5 * fade_factor:
            time.sleep(0.003)

    # 4. 最终归位（增加微小duration，避免瞬间移动导致的超界）
    pyautogui.moveTo(x2, y2, duration=0.1)
    time.sleep(0.15)
    pyautogui.mouseUp()

def human_drag3(x1, y1, x2, y2):
    # 计算总位移
    dist_x = x2 - x1
    dist_y = y2 - y1
    
    # 1. 移动到起点并准备
    pyautogui.moveTo(x1, y1, duration=0.2)
    time.sleep(0.15)
    pyautogui.mouseDown()
    
    # 定义抖动函数
    def get_jitter():
        return random.randint(-1, 1)

    # 阶段 1: 慢启动 (0% - 15%)
    # 步长 1%, 较多延迟
    for p in range(0, 15, 1):
        cur_x = x1 + (dist_x * p // 100) + get_jitter()
        cur_y = y1 + (dist_y * p // 100) + get_jitter()
        pyautogui.moveTo(cur_x, cur_y)
        if random.random() < 0.4:  # 40% 概率延迟
            time.sleep(0.002)

    # 阶段 2: 快速冲刺 (15% - 85%)
    # 随机步长 4-8%, 极低延迟
    p = 15
    while p < 85:
        step = random.randint(4, 8)
        p += step
        if p > 85: p = 85
        
        cur_x = x1 + (dist_x * p // 100)
        cur_y = y1 + (dist_y * p // 100)
        pyautogui.moveTo(cur_x, cur_y)
        
        if random.random() < 0.05: # 5% 模拟手抖微顿
            time.sleep(0.001)

    # 阶段 3: 精准减速与抖动淡出 (85% - 100%)
    # 步长 1%, 随着接近终点，抖动概率线性降低
    for p in range(85, 101, 1):
        # 计算淡出因子: 从 1.0 (85%) 降低到 0.0 (100%)
        fade_factor = (100 - p) / 15.0
        
        jitter_x = 0
        jitter_y = 0
        # 只有在随机值小于淡出因子时才产生抖动
        if random.random() < fade_factor:
            jitter_x = get_jitter()
            jitter_y = get_jitter()

        cur_x = x1 + (dist_x * p // 100) + jitter_x
        cur_y = y1 + (dist_y * p // 100) + jitter_y
        
        pyautogui.moveTo(cur_x, cur_y)
        
        # 延迟也随之稍微调整，模拟最后的微调
        if random.random() < 0.5 * fade_factor:
            time.sleep(0.003)

    # 4. 到达最终点确保位置精确并松开
    pyautogui.moveTo(x2, y2)
    time.sleep(0.15)
    pyautogui.mouseUp()

def human_drag_backup(x1, y1, x2, y2):
    # 计算总位移
    dist_x = x2 - x1
    dist_y = y2 - y1
    
    # 1. 移动到起点并准备
    pyautogui.moveTo(x1, y1, duration=0.2)
    time.sleep(0.15)
    pyautogui.mouseDown()
    
    # 定义抖动函数
    def get_jitter():
        return random.randint(-1, 1)

    # 阶段 1: 慢启动 (0% - 15%)
    # 步长 1%, 较多延迟
    for p in range(0, 15, 1):
        cur_x = x1 + (dist_x * p // 100) + get_jitter()
        cur_y = y1 + (dist_y * p // 100) + get_jitter()
        pyautogui.moveTo(cur_x, cur_y)
        if random.random() < 0.4:  # 40% 概率延迟
            time.sleep(0.002)

    # 阶段 2: 快速冲刺 (15% - 85%)
    # 随机步长 4-8%, 极低延迟
    p = 15
    while p < 85:
        step = random.randint(4, 8)
        p += step
        if p > 85: p = 85
        
        cur_x = x1 + (dist_x * p // 100)
        cur_y = y1 + (dist_y * p // 100)
        pyautogui.moveTo(cur_x, cur_y)
        
        if random.random() < 0.05: # 5% 模拟手抖微顿
            time.sleep(0.001)

    # 阶段 3: 精准减速与抖动淡出 (85% - 100%)
    # 步长 1%, 随着接近终点，抖动概率线性降低
    for p in range(85, 101, 1):
        # 计算淡出因子: 从 1.0 (85%) 降低到 0.0 (100%)
        fade_factor = (100 - p) / 15.0
        
        jitter_x = 0
        jitter_y = 0
        # 只有在随机值小于淡出因子时才产生抖动
        if random.random() < fade_factor:
            jitter_x = get_jitter()
            jitter_y = get_jitter()

        cur_x = x1 + (dist_x * p // 100) + jitter_x
        cur_y = y1 + (dist_y * p // 100) + jitter_y
        
        pyautogui.moveTo(cur_x, cur_y)
        
        # 延迟也随之稍微调整，模拟最后的微调
        if random.random() < 0.5 * fade_factor:
            time.sleep(0.003)

    # 4. 到达最终点确保位置精确并松开
    pyautogui.moveTo(x2, y2)
    time.sleep(0.15)
    pyautogui.mouseUp()