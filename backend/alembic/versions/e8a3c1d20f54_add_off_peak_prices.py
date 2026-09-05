"""add off_peak prices to model_pricings

Revision ID: e8a3c1d20f54
Revises: 724816bcd9aa
Create Date: 2026-09-05 20:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e8a3c1d20f54'
down_revision: Union[str, None] = '724816bcd9aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 空闲时段折扣价：0 = 未设置，计费时沿用标准价
    op.add_column(
        'model_pricings',
        sa.Column('off_peak_input_price', sa.Numeric(precision=12, scale=6),
                  nullable=False, server_default='0'),
    )
    op.add_column(
        'model_pricings',
        sa.Column('off_peak_output_price', sa.Numeric(precision=12, scale=6),
                  nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('model_pricings', 'off_peak_output_price')
    op.drop_column('model_pricings', 'off_peak_input_price')
