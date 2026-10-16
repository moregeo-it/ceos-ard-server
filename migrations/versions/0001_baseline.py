"""Baseline: the schema in production before migrations

Revision ID: 0001
Revises:
Create Date: 2026-10-13 17:58:51.440493
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("identity_provider", sa.Enum("github", "google", name="identityprovider"), nullable=False),
        sa.Column("access_token", sa.String(), nullable=True),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)
        batch_op.create_index(batch_op.f("ix_users_external_id"), ["external_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)

    op.create_table(
        "git_workspaces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("pfs", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("fork_repo_owner", sa.String(length=50), nullable=False),
        sa.Column("fork_repo_name", sa.String(length=50), nullable=False),
        sa.Column("pull_request_number", sa.String(), nullable=True),
        sa.Column("pull_request_status_last_updated_at", sa.DateTime(), nullable=True),
        sa.Column("pull_request_status", sa.Enum("OPEN", "CLOSED", "MERGED", "UNKNOWN", "APPROVED", name="pullrequeststatus"), nullable=True),
        sa.Column("status", sa.Enum("ACTIVE", "ARCHIVED", name="workspacestatus"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("git_workspaces")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
        batch_op.drop_index(batch_op.f("ix_users_external_id"))
        batch_op.drop_index(batch_op.f("ix_users_email"))

    op.drop_table("users")
