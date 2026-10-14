"""Sharing: collaborators, shares and users.last_seen_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13 17:58:59.683367
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_shares",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("mode", sa.Enum("READONLY", name="accessmode"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["git_workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("workspace_shares", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workspace_shares_token"), ["token"], unique=True)

    op.create_table(
        "workspace_collaborators",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("share_id", sa.String(), nullable=True),
        sa.Column("invitee_github_id", sa.String(), nullable=False),
        sa.Column("invitee_github_username", sa.String(), nullable=False),
        sa.Column("invitee_user_id", sa.String(), nullable=True),
        sa.Column("invited_by_user_id", sa.String(), nullable=False),
        sa.Column("mode", sa.Enum("READONLY", name="accessmode"), nullable=False),
        sa.Column("status", sa.Enum("PENDING", "ACCEPTED", name="collaboratorstatus"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["invitee_user_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["share_id"],
            ["workspace_shares.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["git_workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "invitee_github_id", name="uq_collaborator_workspace_github_id"),
    )
    with op.batch_alter_table("workspace_collaborators", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_workspace_collaborators_invited_by_user_id"), ["invited_by_user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workspace_collaborators_invitee_github_id"), ["invitee_github_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workspace_collaborators_invitee_user_id"), ["invitee_user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_workspace_collaborators_share_id"), ["share_id"], unique=False)

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("last_seen_at")

    with op.batch_alter_table("workspace_collaborators", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workspace_collaborators_share_id"))
        batch_op.drop_index(batch_op.f("ix_workspace_collaborators_invitee_user_id"))
        batch_op.drop_index(batch_op.f("ix_workspace_collaborators_invitee_github_id"))
        batch_op.drop_index(batch_op.f("ix_workspace_collaborators_invited_by_user_id"))

    op.drop_table("workspace_collaborators")
    with op.batch_alter_table("workspace_shares", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_workspace_shares_token"))

    op.drop_table("workspace_shares")
