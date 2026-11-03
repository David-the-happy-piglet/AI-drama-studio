"""add durable generation request and worker lease fields

Revision ID: 20260915_02
Revises: 20260915_01
Create Date: 2026-09-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_02"
down_revision: str | Sequence[str] | None = "20260915_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generations") as batch_op:
        batch_op.add_column(
            sa.Column("request_token", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("output_sha256", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("output_bytes", sa.Integer(), nullable=True))

    generations = sa.table(
        "generations",
        sa.column("id", sa.String(length=64)),
        sa.column("request_token", sa.String(length=64)),
    )
    connection = op.get_bind()
    # Legacy generation IDs are stable, unique, non-null, and at most 64 chars.
    # Reusing them produces a deterministic request token on every database.
    connection.execute(
        sa.update(generations)
        .where(generations.c.request_token.is_(None))
        .values(request_token=generations.c.id)
    )

    with op.batch_alter_table("generations") as batch_op:
        batch_op.alter_column(
            "request_token",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_generations_shot_request_token",
            ["shot_id", "request_token"],
        )

    with op.batch_alter_table("production_batch_items") as batch_op:
        batch_op.add_column(
            sa.Column("lease_owner", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_production_batch_items_lease_until",
            ["lease_until"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("production_batch_items") as batch_op:
        batch_op.drop_index("ix_production_batch_items_lease_until")
        batch_op.drop_column("lease_until")
        batch_op.drop_column("lease_owner")

    with op.batch_alter_table("generations") as batch_op:
        batch_op.drop_constraint(
            "uq_generations_shot_request_token",
            type_="unique",
        )
        batch_op.drop_column("output_bytes")
        batch_op.drop_column("output_sha256")
        batch_op.drop_column("request_token")
