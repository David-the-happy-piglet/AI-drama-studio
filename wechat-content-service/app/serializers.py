import json

from .models import Article, AudienceProfile, AutomationPlan, AutomationRun


def audience_dict(audience: AudienceProfile) -> dict:
    return {
        "id": audience.id,
        "slug": audience.slug,
        "name": audience.name,
        "age_range": audience.age_range,
        "description": audience.description,
        "interests": json.loads(audience.interests_json),
        "writing_style": audience.writing_style,
        "title_preferences": json.loads(audience.title_preferences_json),
        "article_length": audience.article_length,
        "image_style": audience.image_style,
        "prohibited_topics": json.loads(audience.prohibited_topics_json),
        "enabled": audience.enabled,
        "created_at": audience.created_at,
        "updated_at": audience.updated_at,
    }


def article_dict(article: Article) -> dict:
    return {
        "id": article.id,
        "audience_profile_id": article.audience_profile_id,
        "topic": article.topic,
        "category": article.category,
        "title_candidates": json.loads(article.title_candidates_json),
        "selected_title": article.selected_title,
        "summary": article.summary,
        "content": json.loads(article.content_json),
        "content_html": article.content_html,
        "status": article.status,
        "llm_provider": article.llm_provider,
        "model_name": article.model_name,
        "prompt_version": article.prompt_version,
        "safety_flags": json.loads(article.safety_flags_json),
        "wechat_media_id": article.wechat_media_id,
        "wechat_publish_id": article.wechat_publish_id,
        "created_at": article.created_at,
        "published_at": article.published_at,
        "assets": [
            {
                "id": asset.id,
                "asset_type": asset.asset_type,
                "position": asset.position,
                "prompt": asset.prompt,
                "local_path": asset.local_path,
                "source_url": asset.source_url,
                "wechat_url": asset.wechat_url,
            }
            for asset in sorted(article.assets, key=lambda item: item.position)
        ],
    }


def automation_plan_dict(plan: AutomationPlan) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "audience_profile_id": plan.audience_profile_id,
        "audience_slug": plan.audience.slug,
        "directions": plan.directions,
        "category": plan.category,
        "provider": plan.provider,
        "run_time": plan.run_time,
        "timezone": plan.timezone,
        "title_count": plan.title_count,
        "image_count": plan.image_count,
        "headline_intensity": plan.headline_intensity,
        "humanize": plan.humanize,
        "auto_upload_draft": plan.auto_upload_draft,
        "enabled": plan.enabled,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def automation_run_dict(run: AutomationRun) -> dict:
    return {
        "id": run.id,
        "automation_plan_id": run.automation_plan_id,
        "article_id": run.article_id,
        "scheduled_for": run.scheduled_for,
        "status": run.status,
        "topic": run.topic,
        "topic_rationale": run.topic_rationale,
        "error_message": run.error_message,
        "retry_count": run.retry_count,
        "next_attempt_at": run.next_attempt_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
    }
