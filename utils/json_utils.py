"""
JSON 解析工具：从 LLM 原始输出中提取和归一化 JSON。
"""
import json
import re


def normalize_dict_keys(obj):
    """递归归一化字典键名，移除多余的引号和空格"""
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if isinstance(k, str):
                new_key = k.strip().strip('"').strip("'").strip()
                new_obj[new_key] = normalize_dict_keys(v)
            else:
                new_obj[k] = normalize_dict_keys(v)
        return new_obj
    elif isinstance(obj, list):
        return [normalize_dict_keys(i) for i in obj]
    else:
        return obj


def extract_json(raw_text: str) -> dict | list:
    """从原始文本中提取 JSON 对象，支持 Markdown 代码块包裹，并具备更强的容错能力"""
    if not raw_text:
        raise ValueError("模型输出为空")

    text = raw_text.strip()

    # 1. 尝试直接解析（最快）
    try:
        return normalize_dict_keys(json.loads(text))
    except json.JSONDecodeError:
        pass

    # 2. 尝试正则提取第一个 {...} 或 [...]
    match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
    if match:
        json_str = match.group(1)

        if json_str.startswith("```json"):
            json_str = json_str[7:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()

        try:
            return normalize_dict_keys(json.loads(json_str))
        except json.JSONDecodeError:
            # 3. 最后的挣扎：处理被截断或格式极其混乱的情况
            try:
                fixed_str = re.sub(r'\\\"', '"', json_str)
                fixed_str = re.sub(r'\'\"(\w+)\"\':', r'"\1":', fixed_str)
                return normalize_dict_keys(json.loads(fixed_str))
            except:
                pass

    raise ValueError(f"无法从输出中提取有效的 JSON。原始输出: {raw_text[:200]}...")
