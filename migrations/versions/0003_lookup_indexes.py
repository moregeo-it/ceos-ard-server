"""Lookup indexes: git_workspaces.user_id and workspace_shares.workspace_id

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14 15:29:56.027264
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_git_workspaces_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("workspace_shares", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workspace_shares_workspace_id"), ["workspace_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("workspace_shares", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workspace_shares_workspace_id"))

    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_git_workspaces_user_id"))
