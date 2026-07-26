from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import GenerationRun


def token_usage_summary(session: Session) -> dict:
    rows = session.execute(
        select(
            GenerationRun.provider,
            GenerationRun.model,
            func.count(GenerationRun.id).label("request_count"),
            func.count(GenerationRun.total_tokens).label("metered_requests"),
            func.coalesce(func.sum(GenerationRun.prompt_tokens), 0).label(
                "prompt_tokens"
            ),
            func.coalesce(func.sum(GenerationRun.completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(GenerationRun.total_tokens), 0).label(
                "total_tokens"
            ),
        )
        .where(
            GenerationRun.step.in_(["article", "humanize"]),
            GenerationRun.status == "completed",
        )
        .group_by(GenerationRun.provider, GenerationRun.model)
        .order_by(GenerationRun.provider, GenerationRun.model)
    ).all()
    models = [
        {
            "provider": row.provider,
            "model": row.model,
            "request_count": int(row.request_count),
            "metered_requests": int(row.metered_requests),
            "prompt_tokens": int(row.prompt_tokens),
            "completion_tokens": int(row.completion_tokens),
            "total_tokens": int(row.total_tokens),
        }
        for row in rows
    ]
    return {
        "models": models,
        "totals": {
            "request_count": sum(item["request_count"] for item in models),
            "metered_requests": sum(item["metered_requests"] for item in models),
            "prompt_tokens": sum(item["prompt_tokens"] for item in models),
            "completion_tokens": sum(item["completion_tokens"] for item in models),
            "total_tokens": sum(item["total_tokens"] for item in models),
        },
    }
