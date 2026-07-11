"""Initial production schema.

Revision ID: 0001_initial
"""

from alembic import op

from data_market_probe.models import Base


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("CREATE INDEX IF NOT EXISTS ix_item_name_trgm ON catalog_item USING gin (name gin_trgm_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_version_description_trgm ON catalog_item_version USING gin (description gin_trgm_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_version_provider_trgm ON catalog_item_version USING gin (provider gin_trgm_ops)")


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
