"""Group projects in workspaces and preserve existing personal ownership."""

import sqlalchemy as sa
from alembic import op

from backend.app.db.encryption import check_storage_key, require_cipher

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    check_storage_key(connection)
    cipher = require_cipher(connection.dialect)
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("personal_user_id", sa.Uuid(), sa.ForeignKey("users.id"), unique=True),
        sa.CheckConstraint("personal_user_id IS NULL OR personal_user_id = id", name="ck_workspace_personal_id"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_workspace_membership_role"),
    )
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])
    connection.execute(
        sa.text("INSERT INTO workspaces (id, name, personal_user_id) SELECT id, :name, id FROM users"),
        {"name": cipher.seal_text("workspaces.name", "Личное пространство")},
    )
    op.execute("INSERT INTO workspace_memberships (workspace_id, user_id, role) SELECT id, id, 'owner' FROM users")
    op.add_column("projects", sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id")))
    op.execute("UPDATE projects SET workspace_id = owner_id WHERE owner_id IS NOT NULL")
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])
    op.create_check_constraint("ck_project_workspace_owner", "projects", "(workspace_id IS NULL) = (owner_id IS NULL)")
    op.drop_constraint("uq_project_owner_repository", "projects", type_="unique")
    op.create_unique_constraint("uq_project_workspace_repository", "projects", ["workspace_id", "repository_key"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM workspaces WHERE personal_user_id IS NULL)")).scalar_one():
        raise RuntimeError("Для отката рабочих пространств восстановите резервную копию до миграции 0009.")
    op.drop_constraint("uq_project_workspace_repository", "projects", type_="unique")
    op.create_unique_constraint("uq_project_owner_repository", "projects", ["owner_id", "repository_key"])
    op.drop_constraint("ck_project_workspace_owner", "projects", type_="check")
    op.drop_index("ix_projects_workspace_id", table_name="projects")
    op.drop_column("projects", "workspace_id")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
