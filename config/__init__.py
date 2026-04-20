"""配置入口：暴露 settings + topology 两个顶层对象。

- `settings: AppSettings` 是类型化真相源（pydantic BaseModel）。访问方式：
    config.settings.system.chrome.chrome_binary
    config.settings.agent.runtime.mode
    config.settings.llm.clients["zenmux"].api_key
- `topology` 是 `Topology` 枚举，由 `AGENT_TOPOLOGY` 环境变量决定。
- `validate()` 启动时做跨 section 的拓扑约束校验；结构与类型校验由 pydantic 构造时完成。

内部拆分：
  _env.py       env 读取小工具
  _chrome.py    本地 Chrome 默认路径
  sections.py   pydantic 模型（AppSettings 及其子节点）
  topology.py   Topology 枚举
"""
from __future__ import annotations

import os

from pydantic import ValidationError

from config._chrome import default_paths as _chrome_default_paths
from config._env import (
    env,
    env_bool,
    env_executable_path,
    env_int,
    load_text_file,
)
from config.sections import AppSettings
from config.topology import Topology


def _build_raw() -> dict:
    """从环境变量与默认值构造原始 dict，pydantic 负责把它变成类型化 settings。"""
    cb, cd_dir, cd_exe = _chrome_default_paths()
    return {
        "system": {
            # screen_size / system_info / chrome.chrome_command 由 bootstrap 在启动时回填。
            "screen_size": {"width": 1920, "height": 1200, "scale": 1.0},
            "system_info": None,
            "screenshot": {"persist": True, "save_dir": "data"},
            "chrome": {
                "debug_port": 9222,
                "chrome_ip": "127.0.0.1",
                "profile_dir": os.path.join(os.path.expanduser("~"), "chrome_debug_profile"),
                "chrome_command": None,
                "chrome_binary": env("CHROME_BINARY", cb),
                "chromedriver_path": env_executable_path("CHROMEDRIVER_PATH", cd_dir, cd_exe),
            },
        },
        "llm": {
            "clients": {
                "doubao": {
                    "base_url": env("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
                    "api_key": env("DOUBAO_API_KEY"),
                    "env_var": "DOUBAO_API_KEY",
                },
                "siliconflow": {
                    "base_url": env("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1"),
                    "api_key": env("SILICONFLOW_API_KEY"),
                    "env_var": "SILICONFLOW_API_KEY",
                },
                "zenmux": {
                    "base_url": env("ZENMUX_BASE_URL", "https://zenmux.ai/api/v1"),
                    "api_key": env("ZENMUX_API_KEY"),
                    "env_var": "ZENMUX_API_KEY",
                },
            },
            "lru_cache_max_size": 10,
        },
        "agent": {
            "default_mode": "waiting",
            "state_limits": {
                "inspiration_pool": 30,
                "title_few_shots": 30,
                "hashtags": 50,
                "recent_searches": 30,
                "learning_notes": 20,
                "anxiety_keywords": 30,
                "knowledge_topics": 30,
                "followers_history": 30,
            },
            "planner": {
                "model": "google/gemini-3-flash-preview",
                "llm_client": "zenmux",
                "temperature": 0.9,
                "max_tokens": 2048,
            },
            "operator": {
                "model": "google/gemini-3-flash-preview",
                "llm_client": "zenmux",
                "temperature": 0.0,
            },
            "page_classifier": {
                "enabled": True,
                "model": "google/gemini-3-flash-preview",
                "llm_client": "zenmux",
                "temperature": 0.0,
                "max_tokens": 4096,
                "record_min_confidence": 0.72,
                "background_enabled": True,
                "background_workers": 1,
                "local_match_enabled": True,
                "local_match_min_score": 0.75,
                "registry_file": "data/page_registry/pages.json",
            },
            "behavior_summarizer": {
                "enabled": True,
                "background_enabled": True,
                "background_workers": 1,
                "model": "google/gemini-3-flash-preview",
                "llm_client": "zenmux",
                "temperature": 0.0,
                "max_tokens": 2048,
                "trace_root": "data/action_traces",
                "candidate_root": "data/action_recipe_candidates",
                "max_events": 8,
                "auto_enable": False,
                "auto_enable_min_confidence": 0.92,
            },
            "persona": {
                "name": "AI 时代职业导航员",
                "character": "极度理性的行业观察者，对'信息差'有种近乎本能的掌控欲。性格沉稳、直击痛点、专业且克制。你认为大模型时代是打工人重启赛道的最佳时机，而你负责提供最真实的准入标准和晋升路径。你对'算法迭代'、'RLHF'、'高阶标注'等关键词有极高的兴奋度。",
                "style": "文案要充满'洞察感'和'降维打击'的专业度。多用'底层逻辑'、'行业窗口期'、'人才画像'、'有效时薪'等词汇。拒绝感性废话，用事实和数据拆解行业现状。评论互动要像资深猎头，冷静、精准、提供解决方案。",
                "strategy": "你是一个深耕 AI 产业链的'职业进化导航员'。你对无意义的职场八卦毫无兴趣，你只关心打工人在 AI 浪潮下的生存与溢价。你擅长剖析传统行业转行 AI 的路径、大模型数据训练的行业内幕以及高质量人才的定价逻辑。你的立场是'理性搞钱，专业避坑'，通过分享行业前瞻洞察和硬核招聘需求，引导高素质人才实现职业赛道的降维打击。",
                "recruitment_info": load_text_file("prompts/recruitment_info.txt", "正在招聘优秀人才"),
            },
            "schedule": {
                "task_goals": {
                    "dm": "查看并回复小红书私信",
                    "cr": "查看并回复小红书评论",
                },
                "max_posts_per_day": 5,
                "daily_schedule": [
                    {"start": "08:00", "end": "22:00", "task": "patrol"},
                ],
                "patrol_rest_between_rounds": [300, 600],
            },
            "storage": {
                "state_file": "data/agent_state.json",
                "save_titles_to_local": True,
                "cleanup": {
                    "enabled": env_bool("AGENT_CLEANUP_ENABLED", True),
                    "max_age_days": env_int("AGENT_CLEANUP_MAX_AGE_DAYS", 7),
                },
            },
            "runtime": {
                "mode": env("AGENT_RUNTIME_MODE", "wuyingcloud") or "wuyingcloud",
                "agentbay": {
                    "api_key": env("AGENTBAY_API_KEY"),
                    "image_id": env("AGENTBAY_IMAGE_ID", "mobile_latest"),
                    "idle_release_timeout": env_int("AGENTBAY_IDLE_RELEASE_TIMEOUT", 600),
                },
                "auto_cleanup_orphans_on_startup": env_bool("AGENT_AUTO_CLEANUP_ORPHANS", True),
                "session_service_url": env("SESSION_SERVICE_URL", ""),
                "session_service_api_key": env("SESSION_SERVICE_API_KEY", ""),
            },
            "accounts": [],
        },
    }


# ── 构造：pydantic 负责结构与类型校验 ─────────────────────────
try:
    settings: AppSettings = AppSettings.model_validate(_build_raw())
except ValidationError as e:
    msgs = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
    raise RuntimeError("配置校验失败:\n  - " + "\n  - ".join(msgs)) from e

# ── topology ─────────────────────────────────────────────────
topology: Topology = Topology(env("AGENT_TOPOLOGY", "monolith") or "monolith")


def validate() -> None:
    """启动跨 section 校验：拓扑与 runtime 的匹配关系（pydantic 无法跨 section 表达）。"""
    errors: list[str] = []

    rt = settings.agent.runtime
    if topology == Topology.BRAIN and not rt.session_service_url:
        errors.append("topology=brain 需要环境变量 SESSION_SERVICE_URL")
    if topology == Topology.SESSION and not rt.agentbay.api_key:
        errors.append("topology=session 需要环境变量 AGENTBAY_API_KEY")
    if rt.mode in ("wuyingcloud", "agentbay") and not rt.session_service_url \
            and not rt.agentbay.api_key:
        errors.append(
            f"runtime.mode={rt.mode} 需要 AGENTBAY_API_KEY 或 SESSION_SERVICE_URL"
        )

    if errors:
        raise RuntimeError("配置校验失败:\n  - " + "\n  - ".join(errors))


__all__ = [
    "AppSettings",
    "Topology",
    "settings",
    "topology",
    "validate",
]
