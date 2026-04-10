"""
Tools 层：Agent 的环境交互能力。
- actions: GUI 交互（click, scroll, paste, drag）
- screenshot: 视觉感知（截屏 + base64）
- cleanup: 环境管理（Chrome 清理）
- screen: 坐标系转换（LLM 0-1000 → 物理像素）
- llm_caller: LLM 统一调用接口
"""
