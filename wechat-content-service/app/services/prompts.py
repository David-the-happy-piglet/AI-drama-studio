import json
from typing import Any

HEADLINE_INTENSITY_GUIDANCE = {
    1: "克制可信：直接说明主题和收益，少用悬念、反问、数字和情绪词。",
    2: "温和吸引：突出实用价值和适用人群，可使用轻度好奇心，但不制造焦虑。",
    3: "标准传播：兼顾可信度与点击欲望，可使用数字、问题或明确收益。",
    4: "强吸引：强化人群代入、反差、悬念和行动暗示，让读者产生立即点开的欲望。",
    5: (
        "最大吸引：综合使用数字、反差、信息缺口、常见误区和对家人的关切，"
        "优先优化点击欲望，但不能恐吓或虚构事实。"
    ),
}


def build_topic_prompt(
    *,
    directions: str,
    category: str,
    audience: dict[str, Any],
    recent_topics: list[str],
    local_date: str,
) -> str:
    recent = "\n".join(f"- {topic}" for topic in recent_topics) or "无"
    output_contract = {
        "topic": "一个具体、可写成单篇文章的主题，不超过80字",
        "category": "内容分类",
        "rationale": "选择理由，不超过100字",
    }
    return f"""
你是微信公众号的选题编辑。请为今天选择一个低风险、实用、具体的文章主题。

日期：{local_date}
内容方向：
{directions}

默认分类：{category}
目标受众：{audience['name']}（{audience['age_range']}岁）
受众兴趣：{"、".join(audience['interests'])}
禁止主题：{"、".join(audience['prohibited_topics'])}

近期已经使用的主题：
{recent}

要求：
1. 选题必须符合内容方向和目标受众，并与近期主题明显不同。
2. 优先选择普通人当天就能实践的生活方式内容。
3. 不追逐未经证实的热点，不诊断疾病，不推荐药品、补充剂剂量或治疗方案。
4. 不使用“根治、排毒、医生不会告诉你”等夸张或伪科学表达。
5. 只返回 JSON，不要 Markdown。

返回结构：
{json.dumps(output_contract, ensure_ascii=False)}
""".strip()


def build_article_prompt(
    *,
    topic: str,
    category: str,
    audience: dict[str, Any],
    title_count: int,
    image_count: int,
    headline_intensity: int = 4,
) -> str:
    try:
        headline_guidance = HEADLINE_INTENSITY_GUIDANCE[headline_intensity]
    except KeyError as exc:
        raise ValueError("headline_intensity must be between 1 and 5") from exc

    output_contract = {
        "title_candidates": [f"{title_count}个标题"],
        "selected_title": "从候选中选出的标题",
        "summary": "不超过120字",
        "opening": "文章开头",
        "sections": [
            {
                "heading": "小标题",
                "paragraphs": ["段落"],
            }
        ],
        "conclusion": "结尾及必要健康提示",
        "image_prompts": [
            {
                "position": 0,
                "asset_type": "cover或inline",
                "prompt": "无文字的中文图片生成提示词",
            }
        ],
    }
    return f"""
为微信公众号生成一篇健康生活文章。

主题：{topic}
分类：{category}
目标受众：{audience['name']}
年龄范围：{audience['age_range']}
受众描述：{audience['description']}
关注领域：{"、".join(audience['interests'])}
写作风格：{audience['writing_style']}
标题偏好：{"；".join(audience['title_preferences'])}
标题吸睛程度：{headline_intensity}/5
标题策略：{headline_guidance}
目标长度：约{audience['article_length']}字
图片风格：{audience['image_style']}
禁止主题：{"、".join(audience['prohibited_topics'])}

要求：
1. 按上述吸睛程度生成{title_count}个明显不同的标题。
   无论等级多高，都不得编造数字、虚构权威、夸大风险、承诺治愈或让标题与正文不符。
2. 选择其中最适合微信公众号传播的一个标题。
3. 正文语言简单，段落短，给出容易执行的建议。
4. 不诊断疾病，不建议停药，不提供具体药物剂量，不虚构医生或患者。
5. 生成{image_count}个无文字图片提示词，第一张为封面。
6. 只返回 JSON，不要 Markdown。

返回结构：
{json.dumps(output_contract, ensure_ascii=False)}
""".strip()
