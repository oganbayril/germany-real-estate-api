"""add scrape_runs.diagnostics

Revision ID: a7c3d9e2f4b1
Revises: e01d7e65d311
Create Date: 2026-10-12 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3d9e2f4b1'
down_revision: Union[str, Sequence[str], None] = 'e01d7e65d311'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('scrape_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('diagnostics', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('scrape_runs', schema=None) as batch_op:
        batch_op.drop_column('diagnostics')
