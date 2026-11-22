"""Identify the provider of each workspace model profile."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace_llm_profiles", sa.Column("provider", sa.String(16), nullable=False, server_default="openai"))
    op.alter_column("workspace_llm_profiles", "api_key", nullable=True)
    op.create_check_constraint("ck_workspace_llm_key", "workspace_llm_profiles",
                               "(provider = 'ollama') = (api_key IS NULL)")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM workspace_llm_profiles WHERE provider <> 'openai')")):
        raise RuntimeError("Перед откатом отключите профили Gemini, Groq и выбранные локальные модели.")
    op.drop_constraint("ck_workspace_llm_key", "workspace_llm_profiles", type_="check")
    op.alter_column("workspace_llm_profiles", "api_key", nullable=False)
    op.drop_column("workspace_llm_profiles", "provider")
