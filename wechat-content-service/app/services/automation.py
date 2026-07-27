import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import (
    Article,
    AutomationPlan,
    AutomationRun,
    GenerationRun,
)
from ..schemas import ArticleGenerateRequest
from .generator import audience_to_context, generate_article
from .llm import build_llm_provider
from .prompts import build_topic_prompt
from .publishing import upload_article_to_draft


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc


def _run_clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("run_time must use HH:MM format") from exc


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def scheduled_datetime(
    plan: AutomationPlan,
    local_date,
) -> datetime:
    local = datetime.combine(
        local_date,
        _run_clock(plan.run_time),
        tzinfo=_timezone(plan.timezone),
    )
    return local.astimezone(UTC)


def enqueue_due_plans(
    session: Session,
    *,
    now: datetime | None = None,
) -> list[int]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    plans = session.scalars(select(AutomationPlan).where(AutomationPlan.enabled.is_(True))).all()
    created: list[int] = []
    for plan in plans:
        local_date = now.astimezone(_timezone(plan.timezone)).date()
        scheduled_for = scheduled_datetime(plan, local_date)
        if scheduled_for > now:
            continue
        run = AutomationRun(
            automation_plan_id=plan.id,
            scheduled_for=scheduled_for,
            status="pending",
            next_attempt_at=now,
        )
        session.add(run)
        try:
            session.commit()
            session.refresh(run)
            created.append(run.id)
        except IntegrityError:
            session.rollback()
    return created


def enqueue_run_now(
    session: Session,
    plan: AutomationPlan,
    *,
    now: datetime | None = None,
) -> AutomationRun:
    now = now or datetime.now(UTC)
    run = AutomationRun(
        automation_plan_id=plan.id,
        scheduled_for=now,
        status="pending",
        next_attempt_at=now,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def recover_stale_runs(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=settings.automation_stale_minutes)
    runs = session.scalars(
        select(AutomationRun).where(
            AutomationRun.status == "running",
            AutomationRun.started_at < cutoff,
        )
    ).all()
    for run in runs:
        run.status = "failed"
        run.error_message = "Worker stopped before completing the run"
        run.next_attempt_at = now
    session.commit()
    return len(runs)


def next_runnable_run(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> AutomationRun | None:
    now = now or datetime.now(UTC)
    return session.scalar(
        select(AutomationRun)
        .where(
            AutomationRun.retry_count < settings.automation_max_retries,
            AutomationRun.status.in_(["pending", "failed"]),
            or_(
                AutomationRun.next_attempt_at.is_(None),
                AutomationRun.next_attempt_at <= now,
            ),
        )
        .order_by(AutomationRun.scheduled_for, AutomationRun.id)
        .limit(1)
    )


def _choose_topic(
    session: Session,
    plan: AutomationPlan,
    settings: Settings,
    local_date: str,
) -> tuple[dict, GenerationRun]:
    recent_topics = session.scalars(
        select(Article.topic)
        .where(Article.audience_profile_id == plan.audience_profile_id)
        .order_by(Article.created_at.desc())
        .limit(30)
    ).all()
    prompt = build_topic_prompt(
        directions=plan.directions,
        category=plan.category,
        audience=audience_to_context(plan.audience),
        recent_topics=list(recent_topics),
        local_date=local_date,
    )
    llm = build_llm_provider(settings, plan.provider)
    started = datetime.now(UTC)
    result = llm.generate_json(
        prompt,
        system_prompt=("你是谨慎的微信公众号选题编辑。只输出合法 JSON，不要输出 Markdown。"),
    )
    for key in ("topic", "category", "rationale"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError(f"Topic response missing valid {key}")
    if len(result["topic"]) > 300:
        raise ValueError("Generated topic is longer than 300 characters")
    if result["topic"] in recent_topics:
        raise ValueError("Generated topic duplicates a recent article")
    generation_run = GenerationRun(
        step="topic",
        provider=llm.name,
        model=llm.model,
        input_text=prompt,
        output_text=json.dumps(result, ensure_ascii=False),
        status="completed",
        duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
        prompt_tokens=llm.usage.prompt_tokens,
        completion_tokens=llm.usage.completion_tokens,
        total_tokens=llm.usage.total_tokens,
    )
    return result, generation_run


def execute_automation_run(
    session: Session,
    run_id: int,
    settings: Settings,
) -> AutomationRun:
    run = session.scalar(
        select(AutomationRun)
        .where(AutomationRun.id == run_id)
        .options(
            selectinload(AutomationRun.plan).selectinload(AutomationPlan.audience),
            selectinload(AutomationRun.article).selectinload(Article.assets),
        )
    )
    if not run:
        raise ValueError(f"Automation run {run_id} not found")
    plan = run.plan

    if not run.article_id:
        local_date = (
            _as_utc(run.scheduled_for).astimezone(_timezone(plan.timezone)).date().isoformat()
        )
        topic_result, topic_generation = _choose_topic(
            session,
            plan,
            settings,
            local_date,
        )
        article = generate_article(
            session,
            ArticleGenerateRequest(
                topic=topic_result["topic"],
                audience_slug=plan.audience.slug,
                category=topic_result.get("category") or plan.category,
                title_count=plan.title_count,
                image_count=plan.image_count,
                target_length=plan.target_length,
                headline_intensity=plan.headline_intensity,
                humanize=plan.humanize,
                provider=plan.provider,
            ),
            plan.audience,
            settings,
        )
        topic_generation.article_id = article.id
        session.add(topic_generation)
        run.article_id = article.id
        run.topic = topic_result["topic"]
        run.topic_rationale = topic_result["rationale"]
        session.commit()
        run.article = article

    article = run.article or session.get(Article, run.article_id)
    if not article:
        raise ValueError("Automation run article could not be loaded")
    if article.status == "needs_review":
        run.status = "needs_review"
        run.error_message = "Article safety scan requires manual review"
        run.completed_at = datetime.now(UTC)
        run.next_attempt_at = None
        session.commit()
        return run

    if plan.auto_upload_draft and not article.wechat_media_id:
        upload_article_to_draft(session, article, settings)

    run.status = "completed"
    run.error_message = None
    run.completed_at = datetime.now(UTC)
    run.next_attempt_at = None
    session.commit()
    session.refresh(run)
    return run


def process_next_run(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> AutomationRun | None:
    now = now or datetime.now(UTC)
    run = next_runnable_run(session, settings, now=now)
    if not run:
        return None
    run.status = "running"
    run.started_at = now
    run.error_message = None
    session.commit()
    run_id = run.id
    try:
        return execute_automation_run(session, run_id, settings)
    except Exception as exc:
        session.rollback()
        failed = session.get(AutomationRun, run_id)
        if not failed:
            raise
        failed.retry_count += 1
        failed.status = "failed"
        failed.error_message = str(exc)[:2000]
        if failed.retry_count < settings.automation_max_retries:
            delay_seconds = min(60 * (2 ** (failed.retry_count - 1)), 3600)
            failed.next_attempt_at = now + timedelta(seconds=delay_seconds)
        else:
            failed.next_attempt_at = None
        session.commit()
        return failed
