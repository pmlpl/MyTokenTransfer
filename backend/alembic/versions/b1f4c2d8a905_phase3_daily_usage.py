"""phase3 daily_usage

Revision ID: b1f4c2d8a905
Revises: a7c2f10d4e8b
Create Date: 2026-09-01 20:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b1f4c2d8a905'
down_revision: Union[str, None] = 'a7c2f10d4e8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('daily_usage',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('key_id', sa.Integer(), nullable=True),
        sa.Column('model', sa.String(length=128), nullable=True),
        sa.Column('calls', sa.Integer(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('total_tokens', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_daily_usage_day', 'daily_usage', ['day'])
    op.create_index('ix_daily_usage_key_id', 'daily_usage', ['key_id'])


def downgrade() -> None:
    op.drop_index('ix_daily_usage_key_id', table_name='daily_usage')
    op.drop_index('ix_daily_usage_day', table_name='daily_usage')
    op.drop_table('daily_usage')
