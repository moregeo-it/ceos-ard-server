"""Workspace types: git_workspaces.type and the upstream repository of each workspace

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02 16:00:00.000000
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.add_column(sa.Column("type", sa.String(length=50), nullable=False, server_default="ceos-ard"))
        batch_op.add_column(sa.Column("upstream_owner", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("upstream_repo", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("upstream_branch", sa.String(length=255), nullable=True))

    # Existing workspaces were all created from the then configured CEOS-ARD repository
    op.execute(
        sa.text("UPDATE git_workspaces SET upstream_owner = :owner, upstream_repo = :repo, upstream_branch = :branch").bindparams(
            owner=os.getenv("CEOS_ARD_ORG", "ceos-org"), repo=os.getenv("CEOS_ARD_REPO", "ceos-ard"), branch=os.getenv("CEOS_ARD_BRANCH", "main")
        )
    )

    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.alter_column("upstream_owner", nullable=False)
        batch_op.alter_column("upstream_repo", nullable=False)
        batch_op.alter_column("upstream_branch", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("git_workspaces", schema=None) as batch_op:
        batch_op.drop_column("upstream_branch")
        batch_op.drop_column("upstream_repo")
        batch_op.drop_column("upstream_owner")
        batch_op.drop_column("type")
