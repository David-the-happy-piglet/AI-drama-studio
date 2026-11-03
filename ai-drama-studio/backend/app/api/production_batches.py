from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from ..models.entities import ProductionBatch, ProductionBatchItem
from ..services import production_batches as service
from .routes import DB, Registry

router = APIRouter(tags=["production-batches"])


@router.post("/episodes/{episode_id}/production-batches/preview")
def preview(episode_id: str, payload: service.BatchRequest, db: DB, registry: Registry):
    return service.preview(db, episode_id, payload, registry)


@router.post("/episodes/{episode_id}/production-batches")
def create(episode_id: str, payload: service.BatchRequest, db: DB, registry: Registry):
    return service.create_batch(db, episode_id, payload, registry)


@router.get("/episodes/{episode_id}/production-batches")
def history(episode_id: str, db: DB):
    return [
        service.read_batch(db, b)
        for b in db.scalars(
            select(ProductionBatch)
            .where(ProductionBatch.episode_id == episode_id)
            .order_by(ProductionBatch.created_at.desc())
            .limit(20)
        ).all()
    ]


@router.post("/production-batches/{batch_id}/stop")
def stop(batch_id: str, db: DB):
    batch = db.get(ProductionBatch, batch_id)
    if not batch:
        raise HTTPException(404, "批量任务不存在")
    db.execute(
        update(ProductionBatchItem)
        .where(
            ProductionBatchItem.batch_id == batch_id,
            ProductionBatchItem.status.in_(("queued", "waiting_keyframe")),
        )
        .values(
            status="skipped",
            active_slot=None,
            lease_owner=None,
            lease_until=None,
            error="用户停止了尚未提交的任务",
        )
    )
    db.commit()
    return service.read_batch(db, batch)
