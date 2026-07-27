import re

RISK_PATTERNS: dict[str, str] = {
    r"根治|彻底治愈|包治": "包含绝对治疗承诺",
    r"不用吃药|立即停药|替代药物": "可能诱导停止或替代正规治疗",
    r"医生不会告诉你|医院不愿告诉你": "包含煽动性医疗表达",
    r"癌细胞最怕|血管全部通了|排出所有毒素": "包含夸大或伪科学表达",
    r"\d+\s*(克|毫克|片|粒).*(每天|每日|一日)": "可能包含具体药物或补充剂剂量",
}


def scan_article(text: str) -> list[str]:
    flags: list[str] = []
    for pattern, message in RISK_PATTERNS.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            flags.append(message)
    return flags
