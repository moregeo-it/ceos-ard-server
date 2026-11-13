"""Comments: comment_threads and comments, and the access mode COMMENT

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06 17:50:45.565176
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODE_TABLES = ("workspace_shares", "workspace_collaborators")
OLD_MODE = sa.Enum("READONLY", name="accessmode")
NEW_MODE = sa.Enum("READONLY", "COMMENT", name="accessmode")


def upgrade() -> None:
    for table in MODE_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("mode", existing_type=OLD_MODE, type_=NEW_MODE, existing_nullable=False)

    op.create_table(
        "comment_threads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=True),
        sa.Column("repository", sa.String(length=201), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("previous_paths", sa.JSON(), nullable=False),
        sa.Column("status", sa.Enum("OPEN", "RESOLVED", name="threadstatus"), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_by_github_id", sa.String(), nullable=False),
        sa.Column("created_by_username", sa.String(), nullable=False),
        sa.Column("resolved_by_user_id", sa.String(), nullable=True),
        sa.Column("resolved_by_github_id", sa.String(), nullable=True),
        sa.Column("resolved_by_username", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["git_workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("comment_threads", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_comment_threads_workspace_id"), ["workspace_id"], unique=False)

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
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["comment_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_comments_thread_id"), ["thread_id"], unique=False)


def downgrade() -> None:
    # The previous version only knows READONLY
    for table in MODE_TABLES:
        op.execute(f"UPDATE {table} SET mode = 'READONLY' WHERE mode = 'COMMENT'")
    for table in MODE_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column("mode", existing_type=NEW_MODE, type_=OLD_MODE, existing_nullable=False)

    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_comments_thread_id"))

    op.drop_table("comments")
    with op.batch_alter_table("comment_threads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_comment_threads_workspace_id"))

    op.drop_table("comment_threads")
