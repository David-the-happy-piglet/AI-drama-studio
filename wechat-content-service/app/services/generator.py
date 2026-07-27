import json
import time
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Article, Asset, AudienceProfile, GenerationRun
from ..schemas import ArticleGenerateRequest
from .humanizer import build_humanizer_prompt
from .images import build_image_provider
from .llm import build_llm_provider
from .prompts import build_article_prompt
from .renderer import render_wechat_html
from .safety import scan_article


def audience_to_context(audience: AudienceProfile) -> dict[str, Any]:
    return {
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
    }


def _validate_result(result: dict[str, Any]) -> None:
    required = {
        "title_candidates",
        "selected_title",
        "summary",
        "opening",
        "sections",
        "conclusion",
        "image_prompts",
    }
    missing = required - set(result)
    if missing:
        raise ValueError(f"LLM response missing keys: {', '.join(sorted(missing))}")
    if not result["title_candidates"] or not result["sections"]:
        raise ValueError("LLM response must contain titles and sections")


def generate_article(
    session: Session,
    request: ArticleGenerateRequest,
    audience: AudienceProfile,
    settings: Settings,
) -> Article:
    audience_context = audience_to_context(audience)
    target_length = request.target_length or audience_context["article_length"]
    audience_context["article_length"] = target_length
    prompt = build_article_prompt(
        topic=request.topic,
        category=request.category,
        audience=audience_context,
        title_count=request.title_count,
        image_count=request.image_count,
        headline_intensity=request.headline_intensity,
    )
    llm = build_llm_provider(settings, request.provider)
    context = {
        "topic": request.topic,
        "category": request.category,
        "audience": audience_context,
        "title_count": request.title_count,
        "image_count": request.image_count,
        "target_length": target_length,
        "headline_intensity": request.headline_intensity,
    }
    started = time.monotonic()
    draft_result = llm.generate_article(
        prompt,
        context,
    )
    _validate_result(draft_result)
    draft_text = json.dumps(draft_result, ensure_ascii=False)
    generation_runs = [
        {
            "step": "article",
            "input_text": prompt,
            "output_text": draft_text,
            "status": "completed",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "prompt_tokens": llm.usage.prompt_tokens,
            "completion_tokens": llm.usage.completion_tokens,
            "total_tokens": llm.usage.total_tokens,
        }
    ]

    result = draft_result
    humanizer_applied = False
    if request.humanize and llm.name != "mock":
        humanizer_prompt = build_humanizer_prompt(draft_result)
        humanizer_started = time.monotonic()
        try:
            humanized = llm.generate_article(humanizer_prompt, context)
            _validate_result(humanized)
            for preserved_key in (
                "title_candidates",
                "selected_title",
                "image_prompts",
            ):
                humanized[preserved_key] = draft_result[preserved_key]
            result = humanized
            humanized_text = json.dumps(humanized, ensure_ascii=False)
            generation_runs.append(
                {
                    "step": "humanize",
                    "input_text": humanizer_prompt,
                    "output_text": humanized_text,
                    "status": "completed",
                    "duration_ms": int((time.monotonic() - humanizer_started) * 1000),
                    "prompt_tokens": llm.usage.prompt_tokens,
                    "completion_tokens": llm.usage.completion_tokens,
                    "total_tokens": llm.usage.total_tokens,
                }
            )
            humanizer_applied = True
        except Exception as exc:  # noqa: BLE001 - retain a usable first draft
            generation_runs.append(
                {
                    "step": "humanize",
                    "input_text": humanizer_prompt,
                    "output_text": "",
                    "status": "failed",
                    "error_message": str(exc),
                    "duration_ms": int((time.monotonic() - humanizer_started) * 1000),
                }
            )

    plain_text = json.dumps(result, ensure_ascii=False)
    safety_flags = scan_article(plain_text)
    status = "needs_review" if safety_flags else "generated"

    article = Article(
        audience_profile_id=audience.id,
        topic=request.topic,
        category=request.category,
        target_length=target_length,
        title_candidates_json=json.dumps(
            result["title_candidates"],
            ensure_ascii=False,
        ),
        selected_title=result["selected_title"],
        summary=result["summary"],
        content_json=plain_text,
        content_html="",
        status=status,
        llm_provider=llm.name,
        model_name=llm.model,
        prompt_version="v2-humanizer-zh" if humanizer_applied else "v2",
        safety_flags_json=json.dumps(safety_flags, ensure_ascii=False),
    )
    session.add(article)
    session.flush()

    for run_data in generation_runs:
        session.add(
            GenerationRun(
                article_id=article.id,
                provider=llm.name,
                model=llm.model,
                **run_data,
            )
        )

    image_provider = build_image_provider(settings)
    asset_urls: list[str] = []
    for index, image_spec in enumerate(result["image_prompts"][: request.image_count]):
        generated = image_provider.generate(
            image_spec["prompt"],
            result["selected_title"],
            index,
            image_spec.get("search_query") or request.topic,
        )
        asset = Asset(
            article_id=article.id,
            asset_type=image_spec.get("asset_type", "inline"),
            position=index,
            prompt=image_spec["prompt"],
            local_path=generated.get("local_path"),
            source_url=generated.get("source_url"),
            source_kind=generated.get("source_kind") or image_provider.name,
            source_page_url=generated.get("source_page_url"),
            source_author=generated.get("source_author"),
            license_name=generated.get("license_name"),
            license_url=generated.get("license_url"),
        )
        session.add(asset)
        if generated.get("source_url"):
            asset_urls.append(str(generated["source_url"]))

    asset_credits = [
        {
            "source_kind": item.source_kind,
            "source_page_url": item.source_page_url,
            "source_author": item.source_author,
            "license_name": item.license_name,
            "license_url": item.license_url,
        }
        for item in sorted(article.assets, key=lambda asset: asset.position)
    ]
    article.content_html = render_wechat_html(
        result,
        asset_urls,
        asset_credits,
    )
    session.commit()
    session.refresh(article)
    return article
