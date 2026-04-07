import json
from typing import List, Dict
import utils.llm_client as llm_client
from agent.skill_loader import extract_json
from prompts.planner_prompt import PLANNER_PROMPT
import config

class Planner:
    def __init__(self, model: str = None, llm_client_name: str = None):
        # 优先从 config 读取配置
        model_config = config.agent.get("planner_model", {})
        
        self.model = model or model_config.get("model", "google/gemini-3-flash-preview")
        self.llm_client_name = llm_client_name or model_config.get("llm_client", "zenmux")
        self.temperature = model_config.get("temperature", 0.2)
        self.max_tokens = model_config.get("max_tokens", 2048)
        self.prompt_template = PLANNER_PROMPT

    def generate_plan(self, user_goal: str, skills: Dict[str, dict], trace_id: str = "system") -> List[dict]:
        """调用 LLM 生成任务规划"""
        # 构建技能简介清单
        manifest = ""
        for name, meta in skills.items():
            manifest += f"- {name}: {meta.get('description', '')}\n"

        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        prompt = self.prompt_template.format(
            SKILL_MANIFEST=manifest,
            USER_GOAL=user_goal,
            CURRENT_TIME=current_time
        )

        try:
            # 引入 _call_llm 统一调用逻辑以记录 Token
            from agent.operator import _call_llm
            plan_data, raw_content = _call_llm(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
                client_name=self.llm_client_name,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                trace_id=trace_id
            )
            return plan_data.get("tasks", [])
        except Exception as e:
            # 兜底：返回一个单任务计划
            return [{"sub_goal": user_goal, "required_skill": None}]
