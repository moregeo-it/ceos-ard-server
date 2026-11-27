"""Baseline: the schema of a fresh PostgreSQL database

Revision ID: 0001
Revises:
Create Date: 2026-11-20 15:49:38.820279
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
        sa.Column(
            "identity_provider",
            sa.Enum("github", "google", name="identityprovider", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("access_token", sa.String(), nullable=True),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("session_version", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("identity_provider", "external_id", name=op.f("uq_users_identity_provider_external_id")),
    )
    op.create_table(
        "git_workspaces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("upstream_owner", sa.String(length=100), nullable=False),
        sa.Column("upstream_repo", sa.String(length=100), nullable=False),
        sa.Column("upstream_branch", sa.String(length=255), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.String(length=50), nullable=False),
        sa.Column("fork_repo_owner", sa.String(length=50), nullable=False),
        sa.Column("fork_repo_name", sa.String(length=50), nullable=False),
        sa.Column("branch_name", sa.String(length=46), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("pull_request_status_last_updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "pull_request_status",
            sa.Enum(
                "OPEN", "CLOSED", "MERGED", "UNKNOWN", "APPROVED", name="pullrequeststatus", native_enum=False, create_constraint=True, length=32
            ),
            nullable=True,
        ),
        sa.Column(
            "status", sa.Enum("ACTIVE", "ARCHIVED", name="workspacestatus", native_enum=False, create_constraint=True, length=32), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_git_workspaces_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_git_workspaces")),
    )
    op.create_index(op.f("ix_git_workspaces_branch_name"), "git_workspaces", ["branch_name"], unique=True)
    op.create_index(op.f("ix_git_workspaces_user_id"), "git_workspaces", ["user_id"], unique=False)
    op.create_table(
        "comment_threads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=True),
        sa.Column("repository", sa.String(length=201), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("previous_paths", sa.JSON(), nullable=False),
        sa.Column("status", sa.Enum("OPEN", "RESOLVED", name="threadstatus", native_enum=False, create_constraint=True, length=32), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_by_github_id", sa.String(), nullable=False),
        sa.Column("created_by_username", sa.String(), nullable=False),
        sa.Column("resolved_by_user_id", sa.String(), nullable=True),
        sa.Column("resolved_by_github_id", sa.String(), nullable=True),
        sa.Column("resolved_by_username", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_comment_threads_created_by_user_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"], ["users.id"], name=op.f("fk_comment_threads_resolved_by_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["git_workspaces.id"], name=op.f("fk_comment_threads_workspace_id_git_workspaces"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comment_threads")),
    )
    op.create_index(op.f("ix_comment_threads_workspace_id"), "comment_threads", ["workspace_id"], unique=False)
    op.create_table(
        "workspace_shares",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("mode", sa.Enum("READONLY", "COMMENT", name="accessmode", native_enum=False, create_constraint=True, length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name=op.f("fk_workspace_shares_created_by_user_id_users")),
        sa.ForeignKeyConstraint(["workspace_id"], ["git_workspaces.id"], name=op.f("fk_workspace_shares_workspace_id_git_workspaces")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_shares")),
    )
    op.create_index(op.f("ix_workspace_shares_token"), "workspace_shares", ["token"], unique=True)
    op.create_index(op.f("ix_workspace_shares_workspace_id"), "workspace_shares", ["workspace_id"], unique=False)
    op.create_table(
        "comments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("author_user_id", sa.String(), nullable=True),
        sa.Column("author_github_id", sa.String(), nullable=False),
        sa.Column("author_username", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], name=op.f("fk_comments_author_user_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["comment_threads.id"], name=op.f("fk_comments_thread_id_comment_threads"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(op.f("ix_comments_thread_id"), "comments", ["thread_id"], unique=False)
    op.create_table(
        "workspace_collaborators",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("share_id", sa.String(), nullable=True),
        sa.Column("invitee_github_id", sa.String(), nullable=False),
        sa.Column("invitee_github_username", sa.String(), nullable=False),
        sa.Column("invitee_user_id", sa.String(), nullable=True),
        sa.Column("invited_by_user_id", sa.String(), nullable=False),
        sa.Column("mode", sa.Enum("READONLY", "COMMENT", name="accessmode", native_enum=False, create_constraint=True, length=32), nullable=False),
        sa.Column(
            "status", sa.Enum("PENDING", "ACCEPTED", name="collaboratorstatus", native_enum=False, create_constraint=True, length=32), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], name=op.f("fk_workspace_collaborators_invited_by_user_id_users")),
        sa.ForeignKeyConstraint(["invitee_user_id"], ["users.id"], name=op.f("fk_workspace_collaborators_invitee_user_id_users")),
        sa.ForeignKeyConstraint(["share_id"], ["workspace_shares.id"], name=op.f("fk_workspace_collaborators_share_id_workspace_shares")),
        sa.ForeignKeyConstraint(["workspace_id"], ["git_workspaces.id"], name=op.f("fk_workspace_collaborators_workspace_id_git_workspaces")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_collaborators")),
        sa.UniqueConstraint("workspace_id", "invitee_github_id", name="uq_collaborator_workspace_github_id"),
    )
    op.create_index(op.f("ix_workspace_collaborators_invited_by_user_id"), "workspace_collaborators", ["invited_by_user_id"], unique=False)
    op.create_index(op.f("ix_workspace_collaborators_invitee_github_id"), "workspace_collaborators", ["invitee_github_id"], unique=False)
    op.create_index(op.f("ix_workspace_collaborators_invitee_user_id"), "workspace_collaborators", ["invitee_user_id"], unique=False)
    op.create_index(op.f("ix_workspace_collaborators_share_id"), "workspace_collaborators", ["share_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workspace_collaborators_share_id"), table_name="workspace_collaborators")
    op.drop_index(op.f("ix_workspace_collaborators_invitee_user_id"), table_name="workspace_collaborators")
    op.drop_index(op.f("ix_workspace_collaborators_invitee_github_id"), table_name="workspace_collaborators")
    op.drop_index(op.f("ix_workspace_collaborators_invited_by_user_id"), table_name="workspace_collaborators")
    op.drop_table("workspace_collaborators")
    op.drop_index(op.f("ix_comments_thread_id"), table_name="comments")
    op.drop_table("comments")
    op.drop_index(op.f("ix_workspace_shares_workspace_id"), table_name="workspace_shares")
    op.drop_index(op.f("ix_workspace_shares_token"), table_name="workspace_shares")
    op.drop_table("workspace_shares")
    op.drop_index(op.f("ix_comment_threads_workspace_id"), table_name="comment_threads")
    op.drop_table("comment_threads")
    op.drop_index(op.f("ix_git_workspaces_user_id"), table_name="git_workspaces")
    op.drop_index(op.f("ix_git_workspaces_branch_name"), table_name="git_workspaces")
    op.drop_table("git_workspaces")
    op.drop_table("users")
