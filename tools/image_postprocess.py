"""
截图后处理：PIL.Image → (bytes, content_type)，供 LLM 输入前做可配置的压缩。

两档 profile 在 `config.settings.system.screenshot.{chromelocal,wuyingcloud}`：
  chromelocal 激进（128 色 + posterize + 饱和度降权）省 token
  wuyingcloud 保守（JPEG q=85）保小字/图标不糊

纯函数 + profile 入参，没有模块级状态；便于单测和两端 env 统一调用。
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageEnhance, ImageOps

from config.sections import ScreenshotProfileCfg


def encode_for_llm(
    img: Image.Image, profile: ScreenshotProfileCfg
) -> tuple[bytes, str]:
    """按 profile 对图像做后处理并编码。返回 (bytes, content_type)。

    管线顺序：palette → saturation → posterize → encode
    - 顺序不要乱改：posterize 放 RGB/饱和度之后，否则 bit 截断会先丢掉饱和度信息。
    - 调用方传入任何 mode 的 PIL.Image 都 OK，内部自动 convert 到 RGB 再走管线。
    """
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        # JPEG 不支持 alpha；统一拍平，避免下游选 JPEG 时报错。
        img = img.convert("RGB")

    if profile.palette_colors:
        img = img.convert("P", palette=Image.ADAPTIVE, colors=profile.palette_colors)
        img = img.convert("RGB")

    if profile.saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(profile.saturation)

    if profile.posterize_bits:
        img = ImageOps.posterize(img, bits=profile.posterize_bits)

    fmt = profile.format.strip().lower()
    if fmt == "jpg":
        fmt = "jpeg"
    buf = BytesIO()
    if fmt == "jpeg":
        img.save(buf, format="JPEG", quality=profile.quality)
    elif fmt == "png":
        img.save(buf, format="PNG")
    else:
        raise RuntimeError(f"不支持的 screenshot format={fmt!r}（仅 jpeg/png）")

    return buf.getvalue(), f"image/{fmt}"


__all__ = ["encode_for_llm"]
