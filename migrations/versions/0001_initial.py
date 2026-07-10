"""initial

Revision ID: 0001
Revises:
Create Date: 2026-07-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("number", sa.String(20), primary_key=True),
        sa.Column("short_description", sa.Text()),
        sa.Column("state", sa.String(100)),
        sa.Column("stage", sa.String(100)),
        sa.Column("opened_at", sa.String(50)),
        sa.Column("updated_at", sa.String(50)),
        sa.Column("requested_for", sa.String(200)),
        sa.Column("category", sa.String(200)),
        sa.Column("request_number", sa.String(20)),
        sa.Column("detail_url", sa.Text()),
        sa.Column("raw_fields", JSONB()),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("tickets")
