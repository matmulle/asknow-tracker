"""add current_stage column

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("current_stage", sa.String(200)))


def downgrade() -> None:
    op.drop_column("tickets", "current_stage")
