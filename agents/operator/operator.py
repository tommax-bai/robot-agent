import os
import json
import time
import config
import traceback
import inspect
import yaml
from datetime import datetime

import utils.logger as logger
import utils.llm_client as llm_client

import core.screenshot as screenshot
import core.actions as actions

from agents.skill.loader import SkillLoader, extract_json
from agents.planner import Planner

from runtime import context

from prompts.action_prompt import ACTION_PROMPT
from utils.token_logger import log_token_usage

def run_task(trace_id: str, user_goal: str):
    """
    主入口：规划 + 执行
    """
    # 0. 任务开始，初始化历史记录
    _init_history(trace_id, user_goal)

    loader = SkillLoader("skills")
    planner = Planner()
    available_skills = _filter_available_skills(loader.skills)
    
    # 1. 任务规划
    logger.info({"msg": "开始任务规划", "goal": user_goal}, trace_id)
    plan = planner.generate_plan(user_goal, available_skills, trace_id)
    logger.info({"msg": "规划完成", "plan": plan}, trace_id)

    # 2. 依次执行子任务
    final_result = {"ok": True, "msg": "所有任务已完成"}
    
    # 获取子 Agent 配置
    operator_config = config.agent.get("operator", {})

    for i, sub_task in enumerate(plan):
        # 检查是否被强杀
        from agents.supervisor import supervisor
        if supervisor.is_aborted(trace_id):
            logger.warning({"msg": "任务执行被 Supervisor 强行中断"}, trace_id)
            return {"ok": False, "error": "Task aborted by supervisor"}

        sub_goal = sub_task["sub_goal"]
        skill_name = sub_task.get("required_skill")

        if skill_name and skill_name not in available_skills:
            logger.warning({
                "msg": "规划结果包含已禁用技能，已跳过该技能",
                "skill": skill_name
            }, trace_id)
            skill_name = None
        
        logger.info({
            "msg": f"执行子任务 {i+1}/{len(plan)}",
            "sub_goal": sub_goal,
            "skill": skill_name
        }, trace_id)

        # 每个子任务允许的最大“续航”次数（防止无限循环）
        max_resumes = 2
        resume_count = 0
        
        while resume_count <= max_resumes:
            result, _ = run_react_loop(
                trace_id=trace_id,
                sub_goal=sub_goal,
                skill_name=skill_name,
                loader=loader,
                model=operator_config.get("model"),
                client_name=operator_config.get("llm_client"),
                temperature=operator_config.get("temperature", 0.0)
            )
            
            if not result.get("ok"):
                if "达到最大步数" in str(result.get("error", "")) and resume_count < max_resumes:
                    resume_count += 1
                    logger.warning({
                        "msg": f"子任务步数超限，正在发起第 {resume_count} 次续航...",
                        "sub_goal": sub_goal
                    }, trace_id)
                    continue
                else:
                    logger.error({"msg": "子任务执行失败", "error": result.get("error")}, trace_id)
                    final_result = result
                    break
            else:
                # 记录成功结果，覆盖默认的 msg，这样 supervisor 才能 harvest 到最后一步的 summary
                final_result = result
                break
        
        if not final_result.get("ok"):
            break

    return final_result

def run_react_loop(trace_id: str, sub_goal: str, skill_name: str, loader: SkillLoader, max_steps: int = 40, model: str = "google/gemini-3-flash-preview", client_name: str = "zenmux", temperature: float = 0.0):
    """
    核心 ReAct 循环 (针对单个子目标)
    """
   
    logger.info("开始ReAct循环")

    # 初始化上下文
    dialogue_history = []
    
    # 构建 System Prompt
    screen_width = config.system["screen_size"]["width"]
    screen_height = config.system["screen_size"]["height"]
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    action = ACTION_PROMPT.format(
        CURRENT_TIME=current_time
    )
    skill_content = ""
    if skill_name:
        skill_content = loader.build_skill_prompt([skill_name])
    

    # 重新定义系统提示词的结构，强化子目标的边界控制
    system_prompt = f"""
        {action}
        {skill_content}

        ## 🎯 当前至高使命 (The Supreme Mission)
        你目前的子任务目标是："{sub_goal}"。

        ### 🚨 绝对指令 (Absolute Constraints)：
        1. **任务达成即停止**：你的所有操作必须【仅】为了达成上述目标。
        2. **严禁过度执行**：即便挂载的“专家技能”中有后续步骤（如登录、点击、输入），只要你视觉确认"{sub_goal}"已经达成，你必须【立即】输出 `finish` 动作。
        3. **完成判定逻辑**：在执行任何动作前，先问自己：“目标是否已达成？”。如果是，调用 `finish`。例如：如果目标是“打开页面”，页面一旦加载完成（即便弹出了登录框），你也必须立即 `finish`，严禁擅自执行登录连招。
    """
    context.agent["messages"]["system"] = {"role": "system", "content": system_prompt}
    #logger.info(context.agent["messages"]["system"])

    last_result = {"ok": True, "message": "任务开始"}

    #清空分析过程
    context.agent["messages"]["user"] = []
    context.agent["messages"]["assistant"] = []
    for step in range(max_steps):
        # 0. 检查是否被强杀 (每一拍都检查)
        from agents.supervisor import supervisor
        if supervisor.is_aborted(trace_id):
            logger.warning({"msg": "ReAct 循环被 Supervisor 强行中断"}, trace_id)
            return {"ok": False, "error": "Task aborted by supervisor"}, dialogue_history

        # 1. 截屏
        image_base64, _, _ = screenshot.get_screenshot_base64(trace_id, include_cursor=True)
        
        # 2. 构造用户消息
        user_text = f"【当前步数: {step}/{max_steps}】\n上一步执行结果: {json.dumps(last_result, ensure_ascii=False)}\n请根据当前截图输出下一步动作 JSON。"
        user_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
        ]

        context.agent["messages"]["user"].append({"role": "user", "content": user_content})

        _max = context.agent["max_rounds"]
        context.agent["messages"]["user"] = context.agent["messages"]["user"][-_max:]

        #注意，[ {system} ], Dict to List
        _message = [context.agent["messages"]["system"]]
        _message += context.agent["messages"]["user"]
        _message += context.agent["messages"]["assistant"]

        # 3. 大脑决策
        try:
            action_obj, raw = _call_llm(
                ###
                messages=_message, 
                model=model, 
                client_name=client_name, 
                trace_id=trace_id,
                temperature=temperature
            )
            # 放弃 print，全部改用 logger.info
            logger.info({"assistant": context.agent["messages"]["assistant"]})
            logger.info({"msg": f"Step {step} LLM Raw Output", "raw": raw}, trace_id)
            
            context.agent["messages"]["assistant"].append({"role": "assistant", "content": raw})

            _max = context.agent["max_rounds"]
            context.agent["messages"]["assistant"] = context.agent["messages"]["assistant"][-_max:]

            # 兼容处理：如果 LLM 返回的是列表（连招），提取第一个动作的 thought
            thought = ""
            if isinstance(action_obj, list) and len(action_obj) > 0:
                thought = action_obj[0].get("thought", "执行连招动作")
            elif isinstance(action_obj, dict):
                thought = action_obj.get("thought", "")

            logger.info({
                "msg": f"Step {step} 决策分析", 
                "thought": thought, 
                "method": action_obj.get("method") if isinstance(action_obj, dict) else "multiple_actions",
                "params": action_obj.get("params") if isinstance(action_obj, dict) else "multiple_params"
            }, trace_id)

            # 记录对话：模型决策
            dialogue_history.append({
                "role": "assistant",
                "thought": thought,
                "action_obj": action_obj
            })
        except Exception as e:
            logger.error({"msg": "LLM 决策或解析失败", "error": str(e), "trace": traceback.format_exc()}, trace_id)
            return {"ok": False, "error": f"LLM 决策失败: {e}"}, dialogue_history

        # --- 核心防线：决策返回后立即再次检查强杀信号 ---
        from agents.supervisor import supervisor
        if supervisor.is_aborted(trace_id):
            logger.warning({"msg": "LLM 已返回决策，但检测到强杀信号，放弃执行动作"}, trace_id)
            return {"ok": False, "error": "Task aborted by supervisor after LLM call"}, dialogue_history
        # --------------------------------------------

        # 4. 执行动作
        # 顺序执行动作列表
        step_results = []
        is_finished = False
        
        # 兼容处理：支持单对象返回或列表返回
        action_list = []
        if isinstance(action_obj, list):
            action_list = action_obj
        elif isinstance(action_obj, dict):
            if action_obj.get("method") == "execute_sequence":
                action_list = action_obj.get("params", {}).get("actions", [])
            elif "method" in action_obj:
                action_list = [action_obj]
            elif "actions" in action_obj and isinstance(action_obj["actions"], list):
                action_list = action_obj["actions"]

        if not action_list:
            logger.error({"msg": "未识别到任何有效动作", "action_obj": action_obj}, trace_id)
            return {"ok": False, "error": "未识别到有效动作"}, dialogue_history

        for idx, current_action in enumerate(action_list):
            # --- 核心防线：连招中场检查 ---
            # 确保在执行长连招（多个动作）的过程中，也能被随时中断
            from agents.supervisor import supervisor
            if supervisor.is_aborted(trace_id):
                logger.warning({"msg": "连招执行中检测到强杀信号，立即中断"}, trace_id)
                return {"ok": False, "error": "Task aborted during action sequence"}, dialogue_history
            # ---------------------------

            method = current_action.get("method")
            params = current_action.get("params", {})

            # --- 暴力防御逻辑：清理 params 里的键名 ---
            if isinstance(params, dict):
                params = {k.strip().strip('"').strip("'").strip(): v for k, v in params.items()}

            try:
                # A. 优先尝试动态加载的自定义工具 (Skill Scripts)
                if method in loader.custom_tools:
                    logger.info({"msg": f"执行动态工具: {method}", "params": params}, trace_id)
                    func = loader.custom_tools[method]
                    func = loader.custom_tools[method]
                    sig = inspect.signature(func)
                    valid_params = {k: v for k, v in params.items() if k in sig.parameters}
                    
                    if 'trace_id' in sig.parameters:
                        action_res = func(trace_id=trace_id, **valid_params)
                    elif 'task_id' in sig.parameters:
                        action_res = func(task_id=trace_id, **valid_params)
                    else:
                        action_res = func(**valid_params)
                    step_results.append({"method": method, "result": action_res})
                
                # B. 结束任务
                elif method == "finish":
                    is_finished = True
                    step_results.append({"method": "finish", "result": params.get("summary")})
                    break
                
                # C. 原子动作
                else:
                    if method in ["click", "dblclick", "move"] and "x" not in params:
                        for k in params.keys():
                            if 'x' in k.lower(): params['x'] = params[k]
                            if 'y' in k.lower(): params['y'] = params[k]
                    
                    action_res = actions.do_actions_step(trace_id, {"method": method, "params": params})
                    step_results.append({"method": method, "result": action_res})

                if len(action_list) > 1 and idx < len(action_list) - 1:
                    wait_time = current_action.get("delay", 0) + 0.5

                    logger.info({"msg": f"连招间隙等待 {wait_time} 秒..."}, trace_id)
                    time.sleep(wait_time)

            except Exception as e:
                error_msg = f"动作执行异常: {str(e)}"
                logger.error({"msg": error_msg, "trace": traceback.format_exc()}, trace_id)
                return {"ok": False, "error": error_msg}, dialogue_history

        # 记录对话：执行观测
        dialogue_history.append({
            "role": "observation",
            "results": step_results
        })

        if is_finished:
            return {"ok": True, "msg": step_results[-1]["result"]}, dialogue_history

        # 5. 更新上下文并截图
        last_result = step_results # 更新给下一步的参考
        time.sleep(0.5)
        # 统一使用 add_user_message 来传递观测结果
        # 注意：图片在下一次循环开始时通过 screenshot.get_screenshot_base64 获取并添加
    
    return {"ok": False, "error": "达到最大步数"}, dialogue_history

def _filter_available_skills(skills: dict) -> dict:
    """根据配置过滤可供 Planner 使用的技能集合。"""
    skill_policy = config.agent.get("skill_selection", {})
    disabled_skill_groups = set(skill_policy.get("disabled_skill_groups", []) )
    disabled_skills = set(skill_policy.get("disabled_skills", []))

    if not disabled_skills and not disabled_skill_groups:
        return skills

    return {
        name: meta
        for name, meta in skills.items()
        if name not in disabled_skills and meta.get("group") not in disabled_skill_groups
    }

def _call_llm(messages: list[dict], model: str, client_name: str, trace_id: str = "system", **kwargs) -> tuple[dict, str]:
    """统一 LLM 调用，包含重试逻辑"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            client = llm_client.get_client(client_name)
            
            # 构建基础参数
            params = {
                "model": model,
                "messages": messages,
                "temperature": kwargs.get("temperature", 0.0),
                "response_format": {"type": "json_object"}
            }
            
            if "max_tokens" in kwargs:
                params["max_tokens"] = kwargs["max_tokens"]

            response = client.chat.completions.create(**params)
            
            # 记录 Token 消耗
            usage = response.usage
            if usage:
                log_token_usage(
                    trace_id=trace_id,
                    model=model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens
                )

            raw = (response.choices[0].message.content or "").strip()
            return extract_json(raw), raw
            
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning({
                    "msg": f"LLM 调用失败，正在进行第 {attempt + 1} 次重试",
                    "error": str(e),
                    "retry_delay": retry_delay
                }, trace_id)
                time.sleep(retry_delay)
                continue
            else:
                logger.error({"msg": "LLM 调用多次重试后依然失败", "error": str(e)}, trace_id)
                raise e

def _init_history(trace_id: str, user_goal: str):
    """任务开始时初始化历史记录：写入 index.jsonl 并创建 trace_id.md 占位"""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. 写入 index.jsonl (索引)
        history_index_path = "history/index.jsonl"
        if not os.path.exists("history"):
            os.makedirs("history")
        entry = {"dt": now, "trace_id": trace_id}
        with open(history_index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
        # 2. 创建 trace_id.md 初始占位
        md_path = f"history/{trace_id}.md"
        meta = {
            "trace_id": trace_id,
            "created_at": now,
            "goal": user_goal,
            "status": "running"
        }
        yaml_content = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        content = f"---\n{yaml_content}---\n\n# Task History: {trace_id}\n"
        
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    except Exception as e:
        logger.error({"msg": "初始化历史记录失败", "error": str(e)})
