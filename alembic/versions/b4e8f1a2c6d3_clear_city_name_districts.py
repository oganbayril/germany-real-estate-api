"""clear districts that are just the city name

Revision ID: b4e8f1a2c6d3
Revises: a7c3d9e2f4b1
Create Date: 2026-10-19 13:00:00.000000

Some immowelt addresses are "Quarter, City (plz)" with no district segment, so
the scraper stored the city name ("Berlin", "Köln") as the district. The
scraper now stores NULL for those; this clears the rows already stored. Data
only, and not reversible (the city name is recoverable from ``city`` anyway).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b4e8f1a2c6d3"
down_revision: Union[str, Sequence[str], None] = "a7c3d9e2f4b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# self-contained copy of realestate.data.features.is_city_name: a migration
# must keep doing the same thing even if app code changes later
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _norm(value: str) -> str:
    return value.strip().lower().translate(_UMLAUTS)


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    listings = sa.table(
        "listings",
        sa.column("id", sa.Integer),
        sa.column("city", sa.String),
        sa.column("district", sa.String),
    )
    rows = conn.execute(
        sa.select(listings.c.id, listings.c.city, listings.c.district).where(
            listings.c.district.is_not(None)
        )
    ).all()
    ids = [r.id for r in rows if _norm(r.district) == _norm(r.city)]
    if ids:
        conn.execute(sa.update(listings).where(listings.c.id.in_(ids)).values(district=None))


def downgrade() -> None:
    """Downgrade schema."""
    # nothing to restore: the cleared value was always equal to the city
