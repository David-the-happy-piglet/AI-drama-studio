import json
from typing import Any

HUMANIZER_VERSION = "humanizer-zh-v1"


def build_humanizer_prompt(article: dict[str, Any]) -> str:
    return f"""
你是一位有多年公众号经验的中文编辑。请按照 Humanizer-zh 的规则，对下面的健康文章
做一次去除 AI 写作痕迹的精修。

必须保留：
1. title_candidates、selected_title 和 image_prompts 原样保留。
2. 所有健康事实、风险提醒、数字、适用条件和行动建议，不得新增未经原文支持的事实。
3. 保持完整 JSON 结构，只输出 JSON，不要解释修改过程或给出评分。

重点改写 summary、opening、sections 和 conclusion：
1. 删除“此外、值得注意的是、至关重要、深入探讨、总而言之”等机械连接词。
2. 避免每段相同句式、连续三段式列举、过多“第一/第二/第三”和否定式排比。
3. 长短句交替；允许自然停顿和口语化表达，但不要故意写错字。
4. 用具体生活动作和场景替代空泛总结，减少宣传腔、正确但无用的套话。
5. 不使用“专家认为”等模糊权威，不虚构个人经历、医生、患者或统计数字。
6. 不过度使用破折号、粗体、表情符号、金句和整齐得像提纲的段落。
7. 结尾落到一件能做的小事或必要提醒，不写“未来可期”式通用积极结论。
8. 适合爱养生的中老年读者：自然、亲切、有分寸，不居高临下。
9. 精修后正文长度保持在原文的 85% 到 110%。
10. 在心里按直接性、节奏、信任度、真实性、精炼度自检，目标至少 45/50，
    但不要把评分写进结果。

待精修文章 JSON：
{json.dumps(article, ensure_ascii=False)}
""".strip()
