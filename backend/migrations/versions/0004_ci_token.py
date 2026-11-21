"""Store the digest of a CI token issued in the editor."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("ci_token_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("workflows", "ci_token_hash")
