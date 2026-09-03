"""add cached_input_price to model_pricings

Revision ID: 5db2d2b10031
Revises: b1f4c2d8a905
Create Date: 2026-09-03 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5db2d2b10031'
down_revision: Union[str, None] = 'b1f4c2d8a905'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'model_pricings',
        sa.Column('cached_input_price', sa.Numeric(precision=12, scale=6),
                  nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('model_pricings', 'cached_input_price')
