"""rednote.publish: 小红书内容发布。纯指令 pack，不挂工具。"""

from __future__ import annotations

from skills import Pack

pack = Pack(
    name="rednote.publish",
    description=(
        "小红书内容发布完整模块。支持从首页开始，经过 AI 文字配图，最终完成标题、正文填写，"
        "设置\"仅自己可见\"后发布笔记。"
    ),
    supports=("local_chrome",),
)
