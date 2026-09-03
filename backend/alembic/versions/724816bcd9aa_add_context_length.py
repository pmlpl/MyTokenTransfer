"""add context_length to model_pricings

Revision ID: 724816bcd9aa
Revises: 5db2d2b10031
Create Date: 2026-09-03 21:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '724816bcd9aa'
down_revision: Union[str, None] = '5db2d2b10031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'model_pricings',
        sa.Column('context_length', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('model_pricings', 'context_length')
