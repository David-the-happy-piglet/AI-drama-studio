import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .database import get_db
from .models import Article, AudienceProfile, AutomationPlan, AutomationRun
from .repositories import create_audience, update_audience
from .schemas import (
    ArticleGenerateRequest,
    AudienceCreate,
    AudienceUpdate,
    AutomationPlanCreate,
)
from .services.automation import enqueue_run_now
from .services.generator import generate_article
from .services.publishing import upload_article_to_draft
from .services.usage import token_usage_summary

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, session: Session = Depends(get_db)):
    audiences = session.scalars(select(AudienceProfile).order_by(AudienceProfile.id)).all()
    articles = session.scalars(
        select(Article)
        .options(selectinload(Article.audience))
        .order_by(Article.created_at.desc())
        .limit(30)
    ).all()
    automation_plans = session.scalars(
        select(AutomationPlan)
        .options(selectinload(AutomationPlan.audience))
        .order_by(AutomationPlan.id)
    ).all()
    automation_runs = session.scalars(
        select(AutomationRun).order_by(AutomationRun.created_at.desc()).limit(15)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "audiences": audiences,
            "articles": articles,
            "token_usage": token_usage_summary(session),
            "automation_plans": automation_plans,
            "automation_runs": automation_runs,
            "audience_created": request.query_params.get("audience_created"),
        },
    )


@router.get("/admin/audiences/new", response_class=HTMLResponse)
def new_audience_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="audience_new.html",
        context={
            "error": request.query_params.get("error"),
        },
    )


@router.post("/admin/audiences")
def create_audience_from_form(
    slug: str = Form(),
    name: str = Form(),
    age_range: str = Form(),
    description: str = Form(""),
    interests: str = Form(""),
    writing_style: str = Form(""),
    title_preferences: str = Form(""),
    article_length: int = Form(1200),
    image_style: str = Form(""),
    prohibited_topics: str = Form(""),
    enabled: bool = Form(False),
    session: Session = Depends(get_db),
):
    try:
        data = AudienceCreate(
            slug=slug.strip().lower(),
            name=name,
            age_range=age_range,
            description=description,
            interests=_lines(interests),
            writing_style=writing_style,
            title_preferences=_lines(title_preferences),
            article_length=article_length,
            image_style=image_style,
            prohibited_topics=_lines(prohibited_topics),
            enabled=enabled,
        )
    except ValidationError:
        return RedirectResponse(
            f"/admin/audiences/new?error={quote('表单内容不符合要求，请检查唯一标识和字数')}",
            status_code=303,
        )
    try:
        audience = create_audience(session, data)
    except IntegrityError:
        session.rollback()
        return RedirectResponse(
            f"/admin/audiences/new?error={quote('该唯一标识已存在，请换一个')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin?audience_created={quote(audience.name)}",
        status_code=303,
    )


@router.get("/admin/audiences/{audience_id}", response_class=HTMLResponse)
def audience_form(
    request: Request,
    audience_id: int,
    session: Session = Depends(get_db),
):
    audience = session.get(AudienceProfile, audience_id)
    if not audience:
        raise HTTPException(status_code=404, detail="Audience not found")
    return templates.TemplateResponse(
        request=request,
        name="audience.html",
        context={
            "audience": audience,
            "interests": "\n".join(json.loads(audience.interests_json)),
            "title_preferences": "\n".join(json.loads(audience.title_preferences_json)),
            "prohibited_topics": "\n".join(json.loads(audience.prohibited_topics_json)),
        },
    )


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


@router.post("/admin/audiences/{audience_id}")
def save_audience(
    audience_id: int,
    name: str = Form(),
    age_range: str = Form(),
    description: str = Form(""),
    interests: str = Form(""),
    writing_style: str = Form(""),
    title_preferences: str = Form(""),
    article_length: int = Form(1200),
    image_style: str = Form(""),
    prohibited_topics: str = Form(""),
    enabled: bool = Form(False),
    session: Session = Depends(get_db),
):
    audience = session.get(AudienceProfile, audience_id)
    if not audience:
        raise HTTPException(status_code=404, detail="Audience not found")
    update_audience(
        session,
        audience,
        AudienceUpdate(
            name=name,
            age_range=age_range,
            description=description,
            interests=_lines(interests),
            writing_style=writing_style,
            title_preferences=_lines(title_preferences),
            article_length=article_length,
            image_style=image_style,
            prohibited_topics=_lines(prohibited_topics),
            enabled=enabled,
        ),
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/generate")
def generate_from_form(
    topic: str = Form(),
    category: str = Form("日常养生"),
    audience_slug: str = Form("senior-wellness"),
    provider: str = Form("mock"),
    target_length: int = Form(0),
    headline_intensity: int = Form(4),
    humanize: bool = Form(False),
    auto_upload_draft: bool = Form(False),
    session: Session = Depends(get_db),
):
    audience = session.scalar(select(AudienceProfile).where(AudienceProfile.slug == audience_slug))
    if not audience:
        raise HTTPException(status_code=404, detail="Audience not found")
    article = generate_article(
        session,
        ArticleGenerateRequest(
            topic=topic,
            category=category,
            audience_slug=audience_slug,
            provider=provider,
            target_length=target_length or None,
            headline_intensity=headline_intensity,
            humanize=humanize,
        ),
        audience,
        settings,
    )
    if auto_upload_draft:
        try:
            media_id, message = upload_article_to_draft(
                session,
                article,
                settings,
            )
        except Exception as exc:  # noqa: BLE001 - show upload result on article page
            return RedirectResponse(
                f"/admin/articles/{article.id}?draft_error={quote(str(exc))}",
                status_code=303,
            )
        detail = quote(f"{message}; media_id={media_id}")
        return RedirectResponse(
            f"/admin/articles/{article.id}?draft_message={detail}",
            status_code=303,
        )
    return RedirectResponse(f"/admin/articles/{article.id}", status_code=303)


def _automation_values(
    *,
    name: str,
    audience_slug: str,
    directions: str,
    category: str,
    provider: str,
    run_time: str,
    timezone: str,
    target_length: int,
    headline_intensity: int,
    humanize: bool,
    auto_upload_draft: bool,
    enabled: bool,
) -> AutomationPlanCreate:
    return AutomationPlanCreate(
        name=name,
        audience_slug=audience_slug,
        directions=directions,
        category=category,
        provider=provider,
        run_time=run_time,
        timezone=timezone,
        target_length=target_length or None,
        headline_intensity=headline_intensity,
        humanize=humanize,
        auto_upload_draft=auto_upload_draft,
        enabled=enabled,
    )


@router.post("/admin/automation-plans")
def create_automation_plan_from_form(
    name: str = Form("每日内容计划"),
    audience_slug: str = Form("senior-wellness"),
    directions: str = Form(),
    category: str = Form("日常养生"),
    provider: str = Form("deepseek"),
    run_time: str = Form("06:30"),
    timezone: str = Form("Asia/Shanghai"),
    target_length: int = Form(0),
    headline_intensity: int = Form(4),
    humanize: bool = Form(False),
    auto_upload_draft: bool = Form(False),
    enabled: bool = Form(False),
    session: Session = Depends(get_db),
):
    data = _automation_values(
        name=name,
        audience_slug=audience_slug,
        directions=directions,
        category=category,
        provider=provider,
        run_time=run_time,
        timezone=timezone,
        target_length=target_length,
        headline_intensity=headline_intensity,
        humanize=humanize,
        auto_upload_draft=auto_upload_draft,
        enabled=enabled,
    )
    audience = session.scalar(
        select(AudienceProfile).where(
            AudienceProfile.slug == data.audience_slug,
            AudienceProfile.enabled.is_(True),
        )
    )
    if not audience:
        raise HTTPException(status_code=404, detail="Audience not found or disabled")
    values = data.model_dump(exclude={"audience_slug"})
    session.add(AutomationPlan(audience_profile_id=audience.id, **values))
    session.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/automation-plans/{plan_id}")
def update_automation_plan_from_form(
    plan_id: int,
    name: str = Form("每日内容计划"),
    audience_slug: str = Form("senior-wellness"),
    directions: str = Form(),
    category: str = Form("日常养生"),
    provider: str = Form("deepseek"),
    run_time: str = Form("06:30"),
    timezone: str = Form("Asia/Shanghai"),
    target_length: int = Form(0),
    headline_intensity: int = Form(4),
    humanize: bool = Form(False),
    auto_upload_draft: bool = Form(False),
    enabled: bool = Form(False),
    session: Session = Depends(get_db),
):
    plan = session.get(AutomationPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Automation plan not found")
    data = _automation_values(
        name=name,
        audience_slug=audience_slug,
        directions=directions,
        category=category,
        provider=provider,
        run_time=run_time,
        timezone=timezone,
        target_length=target_length,
        headline_intensity=headline_intensity,
        humanize=humanize,
        auto_upload_draft=auto_upload_draft,
        enabled=enabled,
    )
    audience = session.scalar(
        select(AudienceProfile).where(
            AudienceProfile.slug == data.audience_slug,
            AudienceProfile.enabled.is_(True),
        )
    )
    if not audience:
        raise HTTPException(status_code=404, detail="Audience not found or disabled")
    values = data.model_dump(exclude={"audience_slug"})
    plan.audience_profile_id = audience.id
    for key, value in values.items():
        setattr(plan, key, value)
    session.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/automation-plans/{plan_id}/run-now")
def run_automation_plan_from_form(
    plan_id: int,
    session: Session = Depends(get_db),
):
    plan = session.get(AutomationPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Automation plan not found")
    enqueue_run_now(session, plan)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/articles/{article_id}/wechat-draft")
def upload_article_draft_from_form(
    article_id: int,
    session: Session = Depends(get_db),
):
    article = session.scalar(
        select(Article).where(Article.id == article_id).options(selectinload(Article.assets))
    )
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    try:
        media_id, message = upload_article_to_draft(
            session,
            article,
            settings,
        )
    except Exception as exc:  # noqa: BLE001 - show the WeChat API error in admin
        return RedirectResponse(
            f"/admin/articles/{article_id}?draft_error={quote(str(exc))}",
            status_code=303,
        )
    detail = quote(f"{message}; media_id={media_id}")
    return RedirectResponse(
        f"/admin/articles/{article_id}?draft_message={detail}",
        status_code=303,
    )


@router.get("/admin/articles/{article_id}", response_class=HTMLResponse)
def article_preview(
    request: Request,
    article_id: int,
    session: Session = Depends(get_db),
):
    article = session.scalar(
        select(Article)
        .where(Article.id == article_id)
        .options(selectinload(Article.assets), selectinload(Article.audience))
    )
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return templates.TemplateResponse(
        request=request,
        name="article.html",
        context={
            "article": article,
            "titles": json.loads(article.title_candidates_json),
            "flags": json.loads(article.safety_flags_json),
            "draft_message": request.query_params.get("draft_message"),
            "draft_error": request.query_params.get("draft_error"),
        },
    )
