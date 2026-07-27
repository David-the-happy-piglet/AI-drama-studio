import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AudienceProfile
from .schemas import AudienceCreate, AudienceUpdate


def audience_values(data: AudienceCreate | AudienceUpdate) -> dict:
    raw = data.model_dump(exclude_unset=True)
    mapping = {
        "interests": "interests_json",
        "title_preferences": "title_preferences_json",
        "prohibited_topics": "prohibited_topics_json",
    }
    values: dict = {}
    for key, value in raw.items():
        target = mapping.get(key, key)
        values[target] = json.dumps(value, ensure_ascii=False) if key in mapping else value
    return values


def create_audience(session: Session, data: AudienceCreate) -> AudienceProfile:
    audience = AudienceProfile(**audience_values(data))
    session.add(audience)
    session.commit()
    session.refresh(audience)
    return audience


def update_audience(
    session: Session,
    audience: AudienceProfile,
    data: AudienceUpdate,
) -> AudienceProfile:
    for key, value in audience_values(data).items():
        setattr(audience, key, value)
    session.commit()
    session.refresh(audience)
    return audience


def seed_audiences(session: Session) -> None:
    exists = session.scalar(select(AudienceProfile.id).limit(1))
    if exists:
        return

    defaults = [
        AudienceCreate(
            slug="senior-wellness",
            name="中老年养生",
            age_range="50-75",
            description="关注日常生活质量、身体舒适度和简单易坚持习惯的人群",
            interests=[
                "睡眠",
                "关节",
                "饮食",
                "心脑血管基础科普",
                "慢性病日常管理",
            ],
            writing_style="亲切、简单、少用术语，短段落，提供明确行动建议",
            title_preferences=[
                "过了50岁……",
                "很多人没注意……",
                "每天坚持这几个习惯……",
            ],
            article_length=1200,
            image_style="温暖明亮、真实自然的中国中老年人物生活场景",
            prohibited_topics=[
                "宣称治愈疾病",
                "建议停药",
                "未经证实的保健品疗效",
            ],
        ),
        AudienceCreate(
            slug="middle-age-health",
            name="中年健康管理",
            age_range="35-55",
            description="工作和家庭压力较大、希望用有限时间改善状态的人群",
            interests=[
                "睡眠不足",
                "职场压力",
                "体重管理",
                "腰颈健康",
                "家庭健康",
            ],
            writing_style="节奏明快，强调时间效率、现实场景和可执行性",
            title_preferences=[
                "人到中年……",
                "每天坐8小时的人……",
                "不是年纪大了，可能是……",
            ],
            article_length=1000,
            image_style="现代都市生活中的中国中年人物，真实自然、干净明亮",
            prohibited_topics=[
                "宣称治愈疾病",
                "制造健康焦虑",
                "建议停药",
            ],
        ),
    ]
    for item in defaults:
        session.add(AudienceProfile(**audience_values(item)))
    session.commit()
