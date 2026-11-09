"""add shot direction and cold-reader reviews

Revision ID: 20261109_03
Revises: 20260915_02
Create Date: 2026-11-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_03"
down_revision: str | Sequence[str] | None = "20260915_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shots") as batch_op:
        batch_op.add_column(
            sa.Column("direction", sa.JSON(), nullable=False, server_default="{}")
        )
    op.create_table(
        "reviews",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=64),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=80), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reviews_project_id", "reviews", ["project_id"])
    op.create_index("ix_reviews_target_id", "reviews", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_reviews_target_id", table_name="reviews")
    op.drop_index("ix_reviews_project_id", table_name="reviews")
    op.drop_table("reviews")
    with op.batch_alter_table("shots") as batch_op:
        batch_op.drop_column("direction")
