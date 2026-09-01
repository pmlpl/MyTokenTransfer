"""phase3 alert_events

Revision ID: a7c2f10d4e8b
Revises: 469ae8701909
Create Date: 2026-09-01 16:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7c2f10d4e8b'
down_revision: Union[str, None] = '469ae8701909'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('alert_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('key_id', sa.Integer(), nullable=True),
        sa.Column('provider_id', sa.Integer(), nullable=True),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('resolved', sa.Boolean(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_alert_events_type'), 'alert_events', ['type'])
    op.create_index(op.f('ix_alert_events_key_id'), 'alert_events', ['key_id'])
    op.create_index(op.f('ix_alert_events_provider_id'), 'alert_events', ['provider_id'])
    op.create_index(op.f('ix_alert_events_resolved'), 'alert_events', ['resolved'])


def downgrade() -> None:
    op.drop_index(op.f('ix_alert_events_resolved'), table_name='alert_events')
    op.drop_index(op.f('ix_alert_events_provider_id'), table_name='alert_events')
    op.drop_index(op.f('ix_alert_events_key_id'), table_name='alert_events')
    op.drop_index(op.f('ix_alert_events_type'), table_name='alert_events')
    op.drop_table('alert_events')
