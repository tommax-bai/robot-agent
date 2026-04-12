---
name: behavior_summarizer
mode: json
---
你是 GUI 行为总结器。请根据一次 subtask 的连续操作轨迹，判断其中是否存在可复用行为，并输出一个候选 recipe。

subtask_goal:
@@SUBTASK_GOAL@@

subtask_intent:
@@SUBTASK_INTENT@@

trace_events:
@@TRACE_EVENTS@@

## Intent 标签 (重要):
recipe 的 `intent` 字段必须使用规范化标签，**必须优先从下方已知列表中选择**；仅当确实无法匹配时才新建（使用英文 snake_case）。
注意：`intent` 应与上方 subtask_intent 保持一致，除非 trace 中的实际行为只覆盖了 subtask 的一个子片段（如 subtask 是"搜索并收割"，但 recipe 只总结了"打开搜索入口"）。

已知 intent 列表：
@@INTENT_CANDIDATES@@

判断规则：
1. 只总结稳定、可验证、可复用的行为，例如打开搜索入口、展开筛选面板、返回列表页、打开发布入口。
2. 不要总结依赖动态内容的行为，例如点击某条笔记卡片、点击某个昵称、点击第 N 个搜索结果，除非目标明确就是通用位置控件。
3. 如果动作只是误点、重复点击同一位置、页面没有变化、或 outcome 显示失败，返回 reusable=false。
4. 如果行为包含任务特定文本，例如某个搜索关键词，默认不要把文本写死进 recipe；优先只总结"打开搜索入口"等前置行为。
5. page_state 使用轨迹中的稳定页面类型。unknown 页面不要生成高置信 recipe。
6. steps 只包含真正可复用的动作，不要包含 finish。
7. click/move/dblclick 动作必须带 locator。若只能复用历史坐标，locator 使用 type=point，x/y 为 0-1000 归一化坐标，并把 confidence 控制在 0.5-0.75。
8. enabled 必须为 false。候选需要人工或评估器提升后才能执行。
9. 如果 trace_events 跨多个 page_state，只选择一个同一 page_state 下的稳定片段进行总结，不要把跨页面流程硬串成一个 recipe。

只输出 JSON：
{
  "reusable": true,
  "reason": "这几步稳定打开搜索入口，目标控件位于页面固定头部区域。",
  "recipe": {
    "id": "rednote_profile_open_search",
    "intent": "open_search",
    "page_state": "rednote_profile",
    "enabled": false,
    "confidence": 0.65,
    "min_confidence": 0.8,
    "required_skill": "rednote-explorer",
    "match_keywords": ["搜索"],
    "summary": "在小红书个人主页打开搜索入口",
    "preconditions": ["当前处于小红书个人主页", "右上角存在搜索图标"],
    "expected": {
      "page_state": "rednote_search_landing",
      "visual_evidence": ["出现搜索输入框"]
    },
    "steps": [
      {
        "method": "click",
        "params": {"description": "点击右上角搜索图标"},
        "locator": {
          "type": "point",
          "x": 915,
          "y": 100,
          "description": "右上角搜索图标"
        }
      }
    ]
  }
}

如果不可复用，输出：
{
  "reusable": false,
  "reason": "原因说明",
  "recipe": null
}
