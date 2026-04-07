import asyncio
import uuid
import time
import random
import re
import os
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any, List
import utils.logger as logger
from agent.operator import run_task
from core.cleanup import cleanup_chrome_environment
import config

class AgentMode(Enum):
    WAITING = "waiting"       # 等待接单状态（静默）
    PATROLLING = "patrolling" # 主动巡逻状态（情报猎人）
    EXECUTING = "executing"   # 正在执行特定任务
    DEBUG = "debug"           # 调试模式（彻底停止所有自动化）

class Supervisor:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Supervisor, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        anget_config = config.agent.get("agent", {})
        
        # self.mode = AgentMode.PATROLLING
        default_mode = anget_config.get("default_mode", "patrolling")

        self.mode = AgentMode.WAITING
        if anget_config.get("default_mode", "patrolling") == "patrolling":
            self.mode = AgentMode.PATROLLING
        else:
            self.mode = AgentMode.WAITING

        self.current_trace_id: Optional[str] = None
        self.current_goal: Optional[str] = None
        self.start_time: float = 0
        self.scheduler_task: Optional[asyncio.Task] = None
        self.aborted_trace_ids: set = set()
        self.today_post_count: int = 0
        self.today_date: str = ""
        
        from skills.rednote.maintainer.scripts.rednote_sync_account_data import get_current_state
        state = get_current_state()
        maint_cfg = anget_config.get("maintenance", {})
        
        if state and "inspiration_pool" in state:
            self.inspiration_pool = list(state["inspiration_pool"])
        else:
            self.inspiration_pool = list(anget_config.get("inspiration_pool", ["职场", "AI工具"]))
            
        if state and "title_few_shots" in state:
            self.title_few_shots = list(state["title_few_shots"])
        else:
            self.title_few_shots = list(maint_cfg.get("title_few_shots", []))

        self.max_pool_size = anget_config.get("inspiration_pool_max_size", 30)
        self.last_discovery = state.get("last_discovery", "") if state else ""
        
        self._initialized = True
        logger.info({"msg": "Supervisor 初始化完成", "mode": self.mode.value})

    def start_scheduler(self):
        if self.scheduler_task is None:
            self.scheduler_task = asyncio.create_task(self.run_schedule_loop())
            logger.info({"msg": "统一调度器已启动"})

    async def run_schedule_loop(self):
        """统一调度引擎：根据 daily_schedule 配置，按时间段分派任务"""
        logger.info({"msg": "统一调度器开始运行"})

        while True:
            try:
                if self.mode == AgentMode.EXECUTING:
                    await asyncio.sleep(10)
                    continue

                if self.mode in [AgentMode.DEBUG, AgentMode.WAITING]:
                    await asyncio.sleep(60)
                    continue

                now = datetime.now()

                today_str = now.strftime("%Y-%m-%d")
                if self.today_date != today_str:
                    self.today_date = today_str
                    self.today_post_count = self._get_today_post_count()

                current_task = self._get_current_task_type(now)

                if current_task is None:
                    logger.info({"msg": f"当前是休息时间 ({now.strftime('%H:%M')})，待机中..."})
                    await asyncio.sleep(300)
                    continue

                if current_task == "patrol":
                    await self._do_patrol_round()

                elif current_task == "dm":
                    await self._do_dm_round()

                elif current_task == "cr":
                    await self._do_cr_round()

                elif current_task == "post":
                    max_posts = config.agent["maintenance"].get("max_posts_per_day", 5)
                    if self.today_post_count >= max_posts:
                        logger.info({
                            "msg": f"今日已发布 {self.today_post_count}/{max_posts} 篇，本时段空闲"
                        })
                        await asyncio.sleep(300)
                    else:
                        posted = await self._do_post_round()
                        if posted:
                            self.today_post_count += 1

            except Exception as e:
                logger.error({"msg": "调度循环异常", "error": str(e)})
                await asyncio.sleep(60)

    async def execute_task(self, user_goal: str, trace_id: str = None):
        if self.mode == AgentMode.DEBUG:
            return {"ok": False, "error": "当前处于调试模式，拒绝执行任务"}
        
        if trace_id is None:
            trace_id = str(uuid.uuid4())

        if self.mode in [AgentMode.PATROLLING, AgentMode.WAITING]:
            if self.current_trace_id:
                self.aborted_trace_ids.add(self.current_trace_id)
                logger.info({"msg": "检测到临时任务，正在强杀当前自动任务", "trace_id": self.current_trace_id})
                cleanup_chrome_environment(trace_id)
        
        prev_mode = self.mode
        self.set_mode(AgentMode.EXECUTING)
        
        self.current_trace_id = trace_id
        self.current_goal = user_goal
        self.start_time = time.time()
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: run_task(trace_id=trace_id, user_goal=user_goal)
            )
            
            # 无论成功失败，只要有 summary 就收割
            summary = ""
            if result:
                if result.get("ok"):
                    summary = result.get("msg", "")
                elif "msg" in result:
                    summary = result.get("msg", "")
            
            if summary:
                self._harvest_knowledge(summary, trace_id)
                
            return result
        finally:
            if trace_id in self.aborted_trace_ids:
                async def delayed_remove(tid):
                    await asyncio.sleep(30)
                    if tid in self.aborted_trace_ids:
                        self.aborted_trace_ids.remove(tid)
                asyncio.create_task(delayed_remove(trace_id))
            
            self.current_trace_id = None
            self.current_goal = None
            self.set_mode(prev_mode)
    
    def set_mode(self, mode: AgentMode):
        old_mode = self.mode
        self.mode = mode
        logger.info({"msg": "Agent 模式切换", "old_mode": old_mode.value, "new_mode": mode.value})
        
        if mode in [AgentMode.DEBUG, AgentMode.WAITING] and self.current_trace_id:
            self.aborted_trace_ids.add(self.current_trace_id)
            logger.info({"msg": "已将当前任务加入强杀名单", "trace_id": self.current_trace_id})

    def is_aborted(self, trace_id: str) -> bool:
        aborted = trace_id in self.aborted_trace_ids
        if aborted:
            logger.debug({"msg": "检测到强杀信号", "trace_id": trace_id})
        return aborted

    def get_status(self) -> Dict[str, Any]:
        task_type = "idle"
        current_task_desc = None
        
        if self.current_trace_id:
            if self.current_trace_id.startswith("patrol-"):
                task_type = "patrol"
                current_task_desc = self.current_goal or "正在进行情报巡逻"
            elif self.current_trace_id.startswith("dm-"):
                task_type = "dm"
                current_task_desc = self.current_goal or "正在处理私信"
            elif self.current_trace_id.startswith("post-"):
                task_type = "post"
                current_task_desc = self.current_goal or "正在执行发帖任务"
            else:
                task_type = "on_demand"
                current_task_desc = self.current_goal or "正在执行指派任务"

        now = datetime.now()
        current_slot = self._get_current_task_type(now)

        return {
            "mode": self.mode.value,
            "is_active": self.current_trace_id is not None,
            "task_type": task_type,
            "current_slot": current_slot or "rest",
            "current_trace_id": self.current_trace_id,
            "current_goal": current_task_desc,
            "today_post_count": self.today_post_count,
            "running_seconds": int(time.time() - self.start_time) if self.start_time > 0 else 0,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S")
        }

    async def _do_patrol_round(self):
        """执行一轮巡逻任务，完成后休息一段时间"""
        trace_id = f"patrol-{str(uuid.uuid4())[:8]}"
        goal = await self._generate_next_patrol_goal()

        logger.info({"msg": "启动巡逻任务", "goal": goal}, trace_id)
        self.current_trace_id = trace_id
        self.current_goal = goal
        self.start_time = time.time()

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: run_task(trace_id=trace_id, user_goal=goal)
            )

            summary = ""
            if result:
                summary = result.get("msg", "")

            if summary:
                self._harvest_knowledge(summary, trace_id)

            logger.info({"msg": "一轮巡逻完成", "result": result}, trace_id)
        except Exception as e:
            logger.error({"msg": "巡逻任务异常", "error": str(e)}, trace_id)
        finally:
            if trace_id in self.aborted_trace_ids:
                async def delayed_remove(tid):
                    await asyncio.sleep(30)
                    self.aborted_trace_ids.discard(tid)
                asyncio.create_task(delayed_remove(trace_id))
            if self.current_trace_id == trace_id:
                self.current_trace_id = None
                self.current_goal = None

        rest_range = config.agent["maintenance"].get(
            "patrol_rest_between_rounds", [300, 600]
        )
        wait = random.randint(rest_range[0], rest_range[1])
        logger.info({"msg": f"巡逻休息 {wait}秒 ({wait/60:.1f}分钟)"})
        await asyncio.sleep(wait)

    async def _do_dm_round(self):
        """执行一轮私信检查与回复"""
        trace_id = f"dm-{str(uuid.uuid4())[:8]}"
        logger.info({"msg": "启动私信处理任务"}, trace_id)
        self.current_trace_id = trace_id
        self.current_goal = "查看并回复小红书私信"
        self.start_time = time.time()

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: run_task(trace_id=trace_id, user_goal="查看并回复小红书私信")
            )
            logger.info({"msg": "私信处理完成", "result": result}, trace_id)
        except Exception as e:
            logger.error({"msg": "私信处理异常", "error": str(e)}, trace_id)
        finally:
            if self.current_trace_id == trace_id:
                self.current_trace_id = None
                self.current_goal = None

        await asyncio.sleep(60)

    async def _do_cr_round(self):
        """执行一轮评论检查与回复"""
        trace_id = f"cr-{str(uuid.uuid4())[:8]}"
        logger.info({"msg": "启动评论处理任务"}, trace_id)
        self.current_trace_id = trace_id
        self.current_goal = "查看并回复小红书评论"
        self.start_time = time.time()

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: run_task(trace_id=trace_id, user_goal="查看并回复小红书评论")
            )
            logger.info({"msg": "评论处理完成", "result": result}, trace_id)
        except Exception as e:
            logger.error({"msg": "评论处理异常", "error": str(e)}, trace_id)
        finally:
            if self.current_trace_id == trace_id:
                self.current_trace_id = None
                self.current_goal = None

        await asyncio.sleep(60)

    async def _do_post_round(self) -> bool:
        """执行一次发帖任务。返回 True 表示成功发帖，False 表示跳过"""
        evo_ctx = self._get_evolution_context()
        if evo_ctx["total_knowledge"] < 50:
            logger.info({
                "msg": "冷启动期，跳过发帖，等待知识积累",
                "knowledge": evo_ctx["total_knowledge"]
            })
            await asyncio.sleep(300)
            return False

        trace_id = f"post-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        goal = await self._generate_posting_goal()
        logger.info({"msg": "启动发帖任务", "goal": goal}, trace_id)
        self.current_trace_id = trace_id
        self.current_goal = goal
        self.start_time = time.time()

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: run_task(trace_id=trace_id, user_goal=goal)
            )

            summary = ""
            if result:
                summary = result.get("msg", "")
            if summary:
                self._harvest_knowledge(summary, trace_id)

            logger.info({"msg": "发帖任务完成", "result": result}, trace_id)
        except Exception as e:
            logger.error({"msg": "发帖任务异常", "error": str(e)}, trace_id)
            return False
        finally:
            if self.current_trace_id == trace_id:
                self.current_trace_id = None
                self.current_goal = None

        await asyncio.sleep(120)
        return True


    async def _generate_posting_goal(self) -> str:
        """生成发帖任务的目标指令（策略驱动：支持动态注入创作逻辑）"""
        try:
            # 1. 先进行头脑风暴获取灵感词
            brainstorm_topics = await self._brainstorm_topics(trace_id="brainstorm")
            # 随机选一个作为本次发帖的主轴
            topic = random.choice(brainstorm_topics)

            from skills.rednote.maintainer.scripts.rednote_sync_account_data import get_current_state
            state = get_current_state() or {}
            
            planner_config = config.agent.get("planner_model", {})
            maint_cfg = config.agent.get("maintenance", {})
            persona = maint_cfg.get("persona", {})
            recruitment_info = maint_cfg.get("recruitment_info", "正在招聘优秀人才")
            strategy = persona.get("writing_strategy", {})
            
            # 随机选取一个发帖风格模板
            template_dir = "prompts/agent"
            templates = [
                os.path.join(template_dir, f)
                for f in os.listdir(template_dir)
                if f.startswith("posting_goal_") and f.endswith(".md")
            ]
            if not templates:
                # 如果模板不存在，回退到默认逻辑
                return f"去小红书调研‘{topic}’，并即兴创作一篇笔记暂存"
                
            template_path = random.choice(templates)
            style_name = os.path.basename(template_path).replace("posting_goal_", "").replace(".md", "")
            logger.info({"msg": f"本次发帖风格: {style_name}", "template": template_path})

            with open(template_path, "r", encoding="utf-8") as f:
                raw_content = f.read()

            # 解析 YAML 元数据
            meta = {}
            body = raw_content
            if raw_content.startswith("---"):
                parts = raw_content.split("---", 2)
                if len(parts) >= 3:
                    import yaml
                    try:
                        meta = yaml.safe_load(parts[1]) or {}
                        body = parts[2].strip()
                    except Exception as e:
                        logger.error({"msg": "YAML解析失败", "error": str(e), "path": template_path})

            # 动态注入变量
            title_few_shots_str = "\n".join(f"- {t}" for t in self.title_few_shots[-10:]) if self.title_few_shots else "（暂无样本，请自由发挥）"
            prompt_content = body.format(
                persona_name=persona.get("name", "AI博主"),
                persona_character=persona.get("character", "专业、理性、有深度"),
                persona_style=persona.get("style", "简洁、有力"),
                topic=topic,
                recruitment_info=recruitment_info,
                title_few_shots=title_few_shots_str,
                last_discovery=self.last_discovery or "（暂无近期收割内容）",
                hook_instruction=strategy.get("hook_template", "爆料一个关于 {topic} 的故事").format(topic=topic),
                transition_logic=strategy.get("transition_logic", "吐槽瓜中主角的专业缺失"),
                recruitment_bridge=strategy.get("recruitment_bridge", "要是团队里有聪明人就好了"),
                call_to_action=strategy.get("call_to_action", "我这儿真的在招人，有兴趣的私。")
            )

            client_name = meta.get("provider", planner_config.get("llm_client", "zenmux"))
            model_name = meta.get("model", planner_config.get("model", "google/gemini-3-flash-preview"))
            
            # 构建请求参数
            request_params = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt_content}],
                "temperature": meta.get("temperature", 0.8)
            }
            # 透传其他 YAML 参数
            for k, v in meta.items():
                if k not in ["provider", "model", "temperature"]:
                    request_params[k] = v

            import utils.llm_client as llm_client
            client_obj = llm_client.get_client(client_name)
            response = client_obj.chat.completions.create(**request_params)
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error({"msg": "生成发帖目标失败", "error": str(e)})
            return "去小红书调研职场热门话题，并即兴创作一篇笔记暂存"

    async def _generate_next_patrol_goal(self) -> str:
        try:
            # 1. 计算进化状态
            evo_ctx = self._get_evolution_context()
            
            # 2. 先进行头脑风暴获取灵感词
            brainstorm_topics = await self._brainstorm_topics(trace_id="brainstorm")
            # 随机选一个作为本次巡逻的主轴
            search_keyword = random.choice(brainstorm_topics)
            
            from skills.rednote.maintainer.scripts.rednote_sync_account_data import get_current_state
            state = get_current_state() or {}
            mood = state.get("mood", "neutral")
            
            planner_config = config.agent.get("planner_model", {})
            maint_cfg = config.agent.get("maintenance", {})
            persona = maint_cfg.get("persona", {})
            
            # 决定本次任务的主轴
            is_home_focus = random.random() < evo_ctx["home_weight"]
            current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 3. 加载外部 prompt 模板
            template_path = "prompts/agent/patrol_goal.md"
            with open(template_path, "r", encoding="utf-8") as f:
                raw_content = f.read()

            # 解析 YAML 元数据
            meta = {}
            body = raw_content
            if raw_content.startswith("---"):
                parts = raw_content.split("---", 2)
                if len(parts) >= 3:
                    import yaml
                    try:
                        meta = yaml.safe_load(parts[1]) or {}
                        body = parts[2].strip()
                    except Exception as e:
                        logger.error({"msg": "patrol_goal模板YAML解析失败", "error": str(e)})

            # 动态注入变量
            prompt = body.format(
                persona_name=persona.get('name', 'AI博主'),
                persona_character=persona.get('character', '专业、理性、有深度'),
                current_time_str=current_time_str,
                total_knowledge=evo_ctx['total_knowledge'],
                home_weight_pct=f"{evo_ctx['home_weight']*100:.0f}",
                search_weight_pct=f"{evo_ctx['search_weight']*100:.0f}",
                focus_desc='【首页沉浸】' if is_home_focus else '【主动探索】',
                search_keyword=search_keyword,
                mood=mood
            )

            client_name = meta.get("provider", planner_config.get("llm_client", "zenmux"))
            model_name = meta.get("model", planner_config.get("model", "google/gemini-3-flash-preview"))
            
            # 构建请求参数
            request_params = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": meta.get("temperature", 0.3)
            }
            # 透传其他 YAML 参数
            for k, v in meta.items():
                if k not in ["provider", "model", "temperature"]:
                    request_params[k] = v

            import utils.llm_client as llm_client
            client_obj = llm_client.get_client(client_name)
            response = client_obj.chat.completions.create(**request_params)
            
            goal = response.choices[0].message.content.strip()
            return goal
        except Exception as e:
            logger.error({"msg": "生成巡逻目标失败", "error": str(e)})
            return f"在小红书首页沉浸浏览，学习爆款逻辑"

    def _harvest_knowledge(self, summary: str, trace_id: str):
        """
        从任务总结中收割知识（爆款标题、学习笔记、心情、灵感、话题词、焦虑点、知识点）。
        支持多种标签格式：[SHOT]、[SHOT]：、【SHOT】等。
        """
        if not summary:
            return

        # 1. 提取爆款标题 [SHOT] 和 对应正文 [CONTENT]
        # 匹配格式：[SHOT] 标题内容 [CONTENT] 正文内容
        note_matches = re.findall(r'[\[【]SHOT[\]】][:：]?\s*(.*?)\s*[\[【]CONTENT[\]】][:：]?\s*(.*?)(?=[\[【]SHOT[\]】]|$)', summary, re.DOTALL)
        
        if note_matches:
            save_enabled = config.agent.get("maintenance", {}).get("save_titles_to_local", False)
            
            for title, content in note_matches:
                title = title.strip()
                content = content.strip()
                if not title: continue
                
                # 更新内存中的 few_shots
                if title not in self.title_few_shots:
                    self.title_few_shots.append(title)
                    self.title_few_shots = self.title_few_shots[-20:]
                
                # 保存到独立文件（含正文）
                if save_enabled and content:
                    self._save_note_detail(title, content)
            
            logger.info({"msg": f"收割到 {len(note_matches)} 条笔记详情"}, trace_id)
        
        # 兼容旧格式提取（如果没有 CONTENT 标签）
        if not note_matches:
            new_shots = re.findall(r'[\[【]SHOT[\]】][:：]?\s*(.+)', summary)
            if new_shots:
                self.title_few_shots = list(set(self.title_few_shots + new_shots))[-20:]
                
                logger.info({"msg": f"收割到 {len(new_shots)} 条爆款标题样本"}, trace_id)

        # 2. 提取学习笔记 [LEARNING]
        new_notes = re.findall(r'[\[【]LEARNING[\]】][:：]?\s*(.+)', summary)
        if new_notes:
            logger.info({"msg": f"收割到 {len(new_notes)} 条深度学习笔记"}, trace_id)

        # 3. 提取话题词 [TAG]
        new_tags = re.findall(r'[\[【]TAG[\]】][:：]?\s*(.+)', summary)
        extracted_tags = []
        for tag_line in new_tags:
            tags = re.findall(r'#(\w+)', tag_line)
            extracted_tags.extend(tags)
        if extracted_tags:
            logger.info({"msg": f"收割到 {len(extracted_tags)} 个热门话题词"}, trace_id)

        # 4. 提取焦虑点 [ANXIETY]
        new_anxieties = re.findall(r'[\[【]ANXIETY[\]】][:：]?\s*(.+)', summary)
        
        # 5. 提取知识点 [KNOWLEDGE]
        new_knowledge = re.findall(r'[\[【]KNOWLEDGE[\]】][:：]?\s*(.+)', summary)

        # 6. 提取心情 [MOOD]
        mood_match = re.search(r'[\[【]MOOD[\]】][:：]?\s*(\w+)', summary)
        new_mood = mood_match.group(1) if mood_match else None

        # 7. 提取灵感 [INSIGHT] (带#话题)
        if "[INSIGHT]" in summary or "【INSIGHT】" in summary:
            insights = re.findall(r'#(\w+)', summary)
            for word in insights:
                if word not in self.inspiration_pool:
                    self.inspiration_pool.append(word)
            if len(self.inspiration_pool) > self.max_pool_size:
                self.inspiration_pool = self.inspiration_pool[-self.max_pool_size:]

        # 8. 持久化到本地
        from skills.rednote.maintainer.scripts.rednote_sync_account_data import rednote_sync_account_data
        self.last_discovery = summary
        
        # 从配置中读取初始值作为兜底，实现“继承”逻辑
        maint_cfg = config.agent.get("maintenance", {})
        
        rednote_sync_account_data(
            inspiration_pool=self.inspiration_pool,
            last_discovery=self.last_discovery,
            title_few_shots=self.title_few_shots,
            learning_notes=new_notes if new_notes else None,
            hashtags=extracted_tags if extracted_tags else None,
            anxiety_keywords=new_anxieties if new_anxieties else None,
            knowledge_topics=new_knowledge if new_knowledge else None,
            mood=new_mood,
            trace_id=trace_id
        )

    def _save_note_detail(self, title: str, content: str):
        """
        将笔记的标题和正文保存到独立的 Markdown 文件中
        """
        try:
            os.makedirs("data/notes", exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"data/notes/{timestamp}.md"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n")
                f.write(f"## 正文内容\n\n{content}\n")
            logger.info({"msg": "笔记详情已保存", "path": filename})
        except Exception as e:
            logger.error({"msg": "保存笔记详情失败", "error": str(e)})

    async def _brainstorm_topics(self, trace_id: str = "system") -> List[str]:
        """
        灵感发散引擎 5.0：从 prompts/agent/brainstorm.md 模板驱动，配置全部来自 config。
        """
        try:
            from skills.rednote.maintainer.scripts.rednote_sync_account_data import get_current_state
            state = get_current_state() or {}
            maint_cfg = config.agent.get("maintenance", {})
            planner_config = config.agent.get("planner_model", {})
            persona = maint_cfg.get("persona", {})

            external_genes = maint_cfg.get("external_genes", ["洞察", "趋势", "红利"])
            random_gene = random.choice(external_genes)

            now = datetime.now()
            time_context = f"当前时间: {now.strftime('%Y-%m-%d %H:%M')}, 星期{now.isoweekday()}"
            time_slots = maint_cfg.get("time_slots", [])
            time_desc = "日常内容创作"
            for slot in time_slots:
                if slot["start"] <= now.hour < slot["end"]:
                    time_desc = slot["desc"]
                    break

            recent_searches = state.get("recent_searches", [])
            forbidden_words = ", ".join(recent_searches[-10:])

            anxiety_pool = state.get("anxiety_keywords", maint_cfg.get("anxiety_keywords", []))
            knowledge_pool = state.get("knowledge_topics", maint_cfg.get("knowledge_topics", []))

            template_path = "prompts/agent/brainstorm.md"
            with open(template_path, "r", encoding="utf-8") as f:
                raw_content = f.read()

            meta = {}
            body = raw_content
            if raw_content.startswith("---"):
                parts = raw_content.split("---", 2)
                if len(parts) >= 3:
                    import yaml
                    try:
                        meta = yaml.safe_load(parts[1]) or {}
                        body = parts[2].strip()
                    except Exception as e:
                        logger.error({"msg": "brainstorm模板YAML解析失败", "error": str(e)})

            prompt = body.format(
                persona_name=persona.get("name", "AI博主"),
                core_strategy=maint_cfg.get("core_strategy", ""),
                time_context=time_context,
                time_desc=time_desc,
                inspiration_keywords=", ".join(self.inspiration_pool[:10]),
                anxiety_keywords=", ".join(anxiety_pool[:8]),
                knowledge_keywords=", ".join(knowledge_pool[:8]),
                random_gene=random_gene,
                forbidden_words=forbidden_words,
            )

            client_name = meta.get("provider", planner_config.get("llm_client", "zenmux"))
            model_name = meta.get("model", planner_config.get("model", "google/gemini-3-flash-preview"))

            request_params = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": meta.get("temperature", 0.9)
            }
            for k, v in meta.items():
                if k not in ["provider", "model", "temperature"]:
                    request_params[k] = v

            import utils.llm_client as llm_client
            client_obj = llm_client.get_client(client_name)
            response = client_obj.chat.completions.create(**request_params)

            raw_topics = response.choices[0].message.content.strip()
            logger.info({"msg": "灵感引擎 5.0 搜索冲动输出", "raw": raw_topics}, trace_id)

            topics = [t.strip() for t in raw_topics.replace("，", ",").split(",") if t.strip()]

            if topics:
                new_search = topics[0]
                from skills.rednote.maintainer.scripts.rednote_sync_account_data import rednote_sync_account_data
                rednote_sync_account_data(recent_searches=[new_search], trace_id=trace_id)

            return topics if topics else ["AI 行业趋势"]

        except Exception as e:
            logger.error({"msg": "头脑风暴失败", "error": str(e)}, trace_id)
            return ["AI 行业趋势", "大模型 职业路径"]

    def _get_current_task_type(self, now: datetime) -> Optional[str]:
        """根据 daily_schedule 配置判断当前应执行的任务类型"""
        schedule = config.agent["maintenance"].get("daily_schedule", [])
        current_time = now.strftime("%H:%M")
        for slot in schedule:
            if slot["start"] <= current_time < slot["end"]:
                return slot["task"]
        return None

    def _get_evolution_context(self) -> Dict[str, Any]:
        """
        计算 Agent 的进化状态与注意力权重。
        基于 agent_state 的丰富程度决定 首页 vs 搜索 的比例。
        """
        try:
            from skills.rednote.maintainer.scripts.rednote_sync_account_data import get_current_state
            state = get_current_state() or {}
            
            # 计算知识储备量
            learning_count = len(state.get("learning_notes", []))
            tag_count = len(state.get("hashtags", []))
            shot_count = len(self.title_few_shots)
            
            total_knowledge = learning_count + tag_count + shot_count
            
            # 动态注意力分配逻辑 (优化版：大幅提高搜索权重)
            # 初始状态 (Knowledge < 100): 首页 10%, 搜索 90%
            # 成熟状态 (Knowledge > 100): 首页 30%, 搜索 70%
            if total_knowledge < 100:
                home_weight = 0.10
            else:
                home_weight = 0.30
                
            search_weight = 1.0 - home_weight
            
            return {
                "home_weight": home_weight,
                "search_weight": search_weight,
                "is_mature": total_knowledge > 30,
                "total_knowledge": total_knowledge
            }
        except Exception:
            return {"home_weight": 0.8, "search_weight": 0.2, "is_mature": False}

    def _get_today_post_count(self) -> int:
        """从持久化状态中读取今日已发帖数"""
        try:
            from skills.rednote.maintainer.scripts.rednote_sync_account_data import get_current_state
            state = get_current_state()
            today_str = datetime.now().strftime("%Y-%m-%d")
            if state and state.get("daily_stats", {}).get("date") == today_str:
                return state["daily_stats"].get("posts_count", 0)
        except Exception:
            pass
        return 0
    
supervisor = Supervisor()
