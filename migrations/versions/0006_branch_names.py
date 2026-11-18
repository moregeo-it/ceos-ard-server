"""Store the workspace branch name; existing workspaces keep workspace/<id>

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.add_column(sa.Column("branch_name", sa.String(length=46), nullable=True))
    op.execute("UPDATE git_workspaces SET branch_name = 'workspace/' || id")
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.alter_column("branch_name", existing_type=sa.String(length=46), nullable=False)
        batch_op.create_index("ix_git_workspaces_branch_name", ["branch_name"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.drop_index("ix_git_workspaces_branch_name")
        batch_op.drop_column("branch_name")
