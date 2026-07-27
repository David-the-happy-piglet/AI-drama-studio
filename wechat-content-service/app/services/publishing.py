from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Article, PublishJob
from .wechat import WechatClient, WechatError


def upload_article_to_draft(
    session: Session,
    article: Article,
    settings: Settings,
    *,
    client: WechatClient | None = None,
) -> tuple[str, str]:
    if article.status == "needs_review" and not settings.allow_flagged_publish:
        raise ValueError("Article has safety flags and cannot be uploaded")

    wechat = client or WechatClient(settings)
    if article.wechat_media_id:
        if wechat.enabled:
            wechat.verify_draft(
                article.wechat_media_id,
                article.selected_title,
            )
            article.status = "wechat_draft"
            session.commit()
            return article.wechat_media_id, "Existing WeChat draft verified"
        return article.wechat_media_id, "Existing mock draft found"

    job = PublishJob(article_id=article.id, job_type="draft", status="running")
    session.add(job)
    session.flush()
    try:
        if wechat.enabled:
            media_id = wechat.create_draft(article)
            message = "Draft uploaded to WeChat"
        else:
            media_id = f"mock-draft-{article.id}"
            message = "WeChat is disabled; mock draft created"
        article.wechat_media_id = media_id
        article.status = "wechat_draft"
        job.wechat_media_id = media_id
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        session.commit()

        if wechat.enabled:
            try:
                wechat.verify_draft(media_id, article.selected_title)
                message = "Draft uploaded to WeChat and media_id verified"
            except WechatError as exc:
                article.status = "wechat_draft_unverified"
                job.status = "completed_unverified"
                job.error_message = str(exc)[:2000]
                session.commit()
                message = f"Draft creation returned media_id, but verification failed: {exc}"
        return media_id, message
    except Exception as exc:
        session.rollback()
        failed_job = PublishJob(
            article_id=article.id,
            job_type="draft",
            status="failed",
            error_message=str(exc)[:2000],
        )
        session.add(failed_job)
        session.commit()
        raise
