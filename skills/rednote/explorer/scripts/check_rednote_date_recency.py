import datetime
import re
import utils.logger as logger

def check_rednote_date_recency(date_text: str, months_limit: int = 1) -> bool:
    """
    检查小红书日期文本是否在指定月数内。
    默认限制为 1 个月。
    支持格式：
    - "刚刚", "1分钟前", "1小时前", "昨天", "前天"
    - "1天前", "2天前" ...
    - "MM-DD" (如 10-24)
    - "YYYY-MM-DD" (如 2024-10-01)
    
    返回: True (新鲜，在限制内), False (太旧)
    """
    now = datetime.datetime.now()
    date_text = date_text.strip()
    
    logger.info({"msg": "开始日期时效性检查", "text": date_text, "limit": months_limit})

    # 1. 处理相对时间 (通常都在3个月内)
    if any(keyword in date_text for keyword in ["刚刚", "分钟前", "小时前", "昨天", "前天", "天前", "周前"]):
        return True

    # 2. 处理 MM-DD 格式 (默认为当年)
    mm_dd_match = re.match(r'^(\d{1,2})-(\d{1,2})$', date_text)
    if mm_dd_match:
        month = int(mm_dd_match.group(1))
        day = int(mm_dd_match.group(2))
        try:
            target_date = now.replace(month=month, day=day)
            # 如果目标月份大于当前月份，说明是去年的
            if month > now.month:
                target_date = target_date.replace(year=now.year - 1)
        except ValueError:
            return False
            
        diff = now - target_date
        return diff.days < (months_limit * 30)

    # 3. 处理 YYYY-MM-DD 格式
    yyyy_mm_dd_match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', date_text)
    if yyyy_mm_dd_match:
        year = int(yyyy_mm_dd_match.group(1))
        month = int(yyyy_mm_dd_match.group(2))
        day = int(yyyy_mm_dd_match.group(3))
        try:
            target_date = datetime.datetime(year, month, day)
        except ValueError:
            return False
            
        diff = now - target_date
        return diff.days < (months_limit * 30)

    # 4. 无法识别的格式，保守起见返回 True 让模型进一步判断或跳过
    logger.warning({"msg": "无法识别的日期格式", "text": date_text})
    return True
