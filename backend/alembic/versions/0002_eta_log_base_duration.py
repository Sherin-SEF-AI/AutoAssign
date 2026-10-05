"""eta log base duration

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eta_log", sa.Column("base_s", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("eta_log", "base_s")
