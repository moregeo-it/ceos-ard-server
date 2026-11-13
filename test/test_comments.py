"""Comment threads on the files of a workspace: roles, threads and replies, deletion, renames, events."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import file_url
from fastapi.testclient import TestClient
from openapi_validation import validate_response

from app.config import settings
from app.db.database import SessionLocal
from app.main import app
from app.models.comment import CommentThread
from app.models.user import IdentityProvider, User
from app.models.workspace import GitWorkspace
from app.models.workspace_share import AccessMode, CollaboratorStatus, WorkspaceCollaborator
from app.services.comment_service import move_threads
from app.services.events_service import event_broker
from app.services.jwt_service import JWTService

FILE = "/glossary/accuracy.yaml"
REQUIREMENT = "/requirements/general/metadata-machine-readable.yaml"


def create_user(username: str, github_id: str) -> User:
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            user = User(
                full_name=username.title(),
                email=f"{username}@example.org",
                username=username,
                external_id=github_id,
                identity_provider=IdentityProvider.github,
                access_token="not-a-token",
                token_expiry=datetime.now(UTC) + timedelta(days=30),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        db.expunge(user)
        return user


@pytest.fixture(scope="module")
def commenter(repositories):
    return create_user("commenter", "5151")


@pytest.fixture(scope="module")
def viewer(repositories):
    return create_user("viewer", "6161")


@pytest.fixture(scope="module")
def stranger(repositories):
    return create_user("stranger", "7171")


def client_of(user: User) -> TestClient:
    token = JWTService.create_access_token(user)["access_token"]
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    client.event_hooks = {"request": [], "response": [validate_response]}
    return client


def share(workspace: dict, owner: User, user: User, mode: AccessMode) -> TestClient:
    """Give `user` access to the workspace, and their client."""
    with SessionLocal() as db:
        db.add(
            WorkspaceCollaborator(
                workspace_id=workspace["id"],
                invitee_github_id=user.external_id,
                invitee_github_username=user.username,
                invitee_user_id=user.id,
                invited_by_user_id=owner.id,
                mode=mode,
                status=CollaboratorStatus.ACCEPTED,
                accepted_at=datetime.now(UTC),
            )
        )
        db.commit()
    return client_of(user)


@pytest.fixture
def events(monkeypatch):
    emitted = []
    monkeypatch.setattr(event_broker, "emit", lambda workspace_id, event_type, **fields: emitted.append((event_type.value, fields)))
    return emitted


def threads_url(workspace: dict, *parts: str) -> str:
    return "/".join([f"/workspaces/{workspace['id']}/threads", *parts])


def start_thread(client: TestClient, workspace: dict, path: str = FILE, body: str = "Is this definition right?") -> dict:
    response = client.post(threads_url(workspace), json={"path": path, "body": body})
    assert response.status_code == 201, response.text
    return response.json()


def threads(client: TestClient, workspace: dict) -> list[dict]:
    response = client.get(threads_url(workspace))
    assert response.status_code == 200, response.text
    return response.json()


def test_start_a_thread(client, user, workspace, events):
    thread = start_thread(client, workspace)

    assert thread["path"] == FILE
    assert thread["status"] == "open"
    assert thread["outdated"] is False
    assert thread["previous_paths"] == []
    assert thread["created_by"] == {"user_id": user.id, "github_id": user.external_id, "username": user.username}
    assert [comment["body"] for comment in thread["comments"]] == ["Is this definition right?"]
    assert threads(client, workspace) == [thread]

    assert events == [("thread.created", {"actor_user_id": user.id, "path": FILE, "thread": thread})]
    with SessionLocal() as db:
        assert db.get(CommentThread, thread["id"]).repository == "ceos-org/ceos-ard"


@pytest.mark.parametrize(
    "path,code",
    [("/glossary", 400), ("/glossary/missing.yaml", 404), ("../outside.yaml", 400)],
)
def test_threads_are_on_files(client, workspace, path, code):
    response = client.post(threads_url(workspace), json={"path": path, "body": "Comment"})
    assert response.status_code == code, response.text


def test_empty_comments_are_refused(client, workspace):
    response = client.post(threads_url(workspace), json={"path": FILE, "body": "   "})
    assert response.status_code == 422, response.text


def test_roles(client, user, workspace, commenter, viewer, stranger):
    commenting = share(workspace, user, commenter, AccessMode.COMMENT)
    viewing = share(workspace, user, viewer, AccessMode.READONLY)

    assert commenting.get(f"/workspaces/{workspace['id']}").json()["viewer_role"] == "comment"

    # Commenters start threads, reply and resolve
    thread = start_thread(commenting, workspace)
    response = commenting.post(threads_url(workspace, thread["id"], "comments"), json={"body": "A reply"})
    assert response.status_code == 201, response.text
    response = commenting.patch(threads_url(workspace, thread["id"]), json={"status": "resolved"})
    assert response.status_code == 200, response.text
    assert response.json()["resolved_by"]["username"] == "commenter"

    # Viewers only read
    assert threads(viewing, workspace)[0]["id"] == thread["id"]
    response = viewing.post(threads_url(workspace), json={"path": FILE, "body": "Comment"})
    assert response.status_code == 403, response.text
    response = viewing.post(threads_url(workspace, thread["id"], "comments"), json={"body": "Reply"})
    assert response.status_code == 403, response.text
    response = viewing.patch(threads_url(workspace, thread["id"]), json={"status": "open"})
    assert response.status_code == 403, response.text

    # Others don't learn that the workspace exists
    response = client_of(stranger).get(threads_url(workspace))
    assert response.status_code == 404, response.text

    # Commenters still can't change files
    response = commenting.delete(file_url(workspace, "files", FILE))
    assert response.status_code == 403, response.text


def test_reopen_clears_the_resolution(client, workspace):
    thread = start_thread(client, workspace)
    client.patch(threads_url(workspace, thread["id"]), json={"status": "resolved"})

    response = client.patch(threads_url(workspace, thread["id"]), json={"status": "open"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "open"
    assert response.json()["resolved_by"] is None
    assert response.json()["resolved_at"] is None


def test_only_authors_edit_their_comments(client, user, workspace, commenter, events):
    commenting = share(workspace, user, commenter, AccessMode.COMMENT)
    thread = start_thread(commenting, workspace)
    comment_url = threads_url(workspace, thread["id"], "comments", thread["comments"][0]["id"])

    response = client.patch(comment_url, json={"body": "Changed by the owner"})
    assert response.status_code == 403, response.text

    response = commenting.patch(comment_url, json={"body": "Is this definition still right?"})
    assert response.status_code == 200, response.text
    comment = response.json()["comments"][0]
    assert comment["body"] == "Is this definition still right?"
    assert comment["edited_at"] is not None
    assert events[-1] == ("thread.updated", {"actor_user_id": commenter.id, "path": FILE, "thread": response.json()})


def test_delete_comments(client, user, workspace, commenter, events):
    commenting = share(workspace, user, commenter, AccessMode.COMMENT)
    thread = start_thread(client, workspace)
    first = thread["comments"][0]["id"]
    response = commenting.post(threads_url(workspace, thread["id"], "comments"), json={"body": "A reply"})
    reply = response.json()["comments"][1]["id"]

    # Commenters only delete their own comments
    response = commenting.delete(threads_url(workspace, thread["id"], "comments", first))
    assert response.status_code == 403, response.text

    # The first comment leaves a placeholder while there are replies
    response = client.delete(threads_url(workspace, thread["id"], "comments", first))
    assert response.status_code == 200, response.text
    placeholder = response.json()["comments"][0]
    assert placeholder["deleted"] is True
    assert placeholder["body"] is None
    response = client.patch(threads_url(workspace, thread["id"], "comments", first), json={"body": "Back"})
    assert response.status_code == 404, response.text

    # The owner deletes any comment; with the last one the thread goes
    response = client.delete(threads_url(workspace, thread["id"], "comments", reply))
    assert response.status_code == 204, response.text
    assert threads(client, workspace) == []
    assert events[-1] == ("thread.deleted", {"actor_user_id": user.id, "path": FILE, "thread_id": thread["id"]})


def test_comments_with_replies_after_them_become_placeholders(client, workspace, events):
    thread = start_thread(client, workspace)
    for body in ["First reply", "Second reply"]:
        response = client.post(threads_url(workspace, thread["id"], "comments"), json={"body": body})
    first, middle, last = [comment["id"] for comment in response.json()["comments"]]

    response = client.delete(threads_url(workspace, thread["id"], "comments", middle))
    assert response.status_code == 200, response.text
    assert [comment["deleted"] for comment in response.json()["comments"]] == [False, True, False]

    # The last one goes with the placeholders directly before it
    response = client.delete(threads_url(workspace, thread["id"], "comments", last))
    assert response.status_code == 200, response.text
    assert [comment["id"] for comment in response.json()["comments"]] == [first]

    response = client.delete(threads_url(workspace, thread["id"], "comments", first))
    assert response.status_code == 204, response.text
    assert threads(client, workspace) == []
    assert events[-1][0] == "thread.deleted"


def test_threads_follow_renames_case_sensitively(workspace):
    with SessionLocal() as db:
        for path in ["/requirements/Foo/a.yaml", "/requirements/foo/b.yaml"]:
            db.add(
                CommentThread(
                    workspace_id=workspace["id"], repository="ceos-org/ceos-ard", path=path, created_by_github_id="1", created_by_username="x"
                )
            )
        db.commit()
        move_threads(db, workspace["id"], "/requirements/Foo", "/requirements/Bar")
        paths = sorted(thread.path for thread in db.query(CommentThread).filter(CommentThread.workspace_id == workspace["id"]))
    assert paths == ["/requirements/Bar/a.yaml", "/requirements/foo/b.yaml"]


def test_threads_follow_renames(client, workspace, events):
    on_file = start_thread(client, workspace, REQUIREMENT)
    elsewhere = start_thread(client, workspace, FILE)

    response = client.patch(file_url(workspace, "files", REQUIREMENT), json={"operation": "rename", "target": "renamed.yaml"})
    assert response.status_code == 200, response.text
    response = client.patch(file_url(workspace, "files", "/requirements/general"), json={"operation": "rename", "target": "common"})
    assert response.status_code == 200, response.text

    by_id = {thread["id"]: thread for thread in threads(client, workspace)}
    assert by_id[on_file["id"]]["path"] == "/requirements/common/renamed.yaml"
    assert by_id[on_file["id"]]["previous_paths"] == [REQUIREMENT, "/requirements/general/renamed.yaml"]
    assert by_id[on_file["id"]]["outdated"] is False
    assert by_id[elsewhere["id"]]["path"] == FILE


def test_threads_follow_a_reverted_rename(client, workspace):
    thread = start_thread(client, workspace)
    client.patch(file_url(workspace, "files", FILE), json={"operation": "rename", "target": "precision.yaml"})

    response = client.patch(file_url(workspace, "files", "/glossary/precision.yaml"), json={"operation": "revert"})
    assert response.status_code == 200, response.text
    assert response.json()["path"] == FILE
    assert threads(client, workspace)[0]["id"] == thread["id"]
    assert threads(client, workspace)[0]["path"] == FILE


def test_threads_follow_a_rename_reverted_through_another_spelling(client, workspace):
    start_thread(client, workspace)
    client.patch(file_url(workspace, "files", FILE), json={"operation": "rename", "target": "precision.yaml"})

    response = client.patch(file_url(workspace, "files", "/glossary//precision.yaml"), json={"operation": "revert"})
    assert response.status_code == 200, response.text
    assert threads(client, workspace)[0]["path"] == FILE


def test_threads_on_deleted_files_are_outdated(client, workspace):
    start_thread(client, workspace)

    client.delete(file_url(workspace, "files", FILE))
    assert threads(client, workspace)[0]["outdated"] is True

    client.patch(file_url(workspace, "files", FILE), json={"operation": "revert"})
    assert threads(client, workspace)[0]["outdated"] is False


def test_comments_are_read_only_when_archived(client, workspace):
    thread = start_thread(client, workspace)
    response = client.patch(f"/workspaces/{workspace['id']}", json={"status": "archived"})
    assert response.status_code == 200, response.text

    assert len(threads(client, workspace)) == 1
    response = client.post(threads_url(workspace), json={"path": FILE, "body": "Comment"})
    assert response.status_code == 403, response.text
    response = client.patch(threads_url(workspace, thread["id"]), json={"status": "resolved"})
    assert response.status_code == 403, response.text


def test_no_thread_on_a_workspace_archived_while_waiting_for_the_lock(client, workspace, monkeypatch):
    from app.db.database import SessionLocal
    from app.models.workspace import GitWorkspace, WorkspaceStatus
    from app.services import comment_service

    run_exclusive = comment_service.run_exclusive

    # The owner archives the workspace, in a request of its own, while this one waits
    async def archive_first(workspace_id, transaction):
        with SessionLocal() as db:
            db.get(GitWorkspace, workspace_id).status = WorkspaceStatus.ARCHIVED
            db.commit()
        return await run_exclusive(workspace_id, transaction)

    monkeypatch.setattr(comment_service, "run_exclusive", archive_first)
    response = client.post(threads_url(workspace), json={"path": FILE, "body": "Comment"})
    assert response.status_code == 403, response.text
    assert threads(client, workspace) == []


def test_comments_outlive_the_workspace(client, workspace):
    thread = start_thread(client, workspace)
    # The workspace row goes as DELETE /workspaces/{id} and the cleanup script remove it, through
    # the ORM. Not through the API: on Windows, removing the git repository's files fails.
    with SessionLocal() as db:
        db.delete(db.get(GitWorkspace, workspace["id"]))
        db.commit()

    with SessionLocal() as db:
        kept = db.get(CommentThread, thread["id"])
        assert kept.workspace_id is None
        assert kept.repository == "ceos-org/ceos-ard"
        assert kept.path == FILE
        assert [comment.body for comment in kept.comments] == ["Is this definition right?"]


def test_share_links_grant_comment_mode(client, workspace, stranger, monkeypatch):
    # Set explicitly: a local .env may enable it already
    monkeypatch.setattr(settings, "SHARING_MODES_ENABLED", ["readonly"])
    response = client.post(f"/workspaces/{workspace['id']}/shares", json={"mode": "comment"})
    assert response.status_code == 400, response.text

    monkeypatch.setattr(settings, "SHARING_MODES_ENABLED", ["readonly", "comment"])
    response = client.post(f"/workspaces/{workspace['id']}/shares", json={"mode": "comment"})
    assert response.status_code == 201, response.text
    token = response.json()["url"].rsplit("/", 1)[-1]

    joining = client_of(stranger)
    response = joining.post(f"/shares/{token}/redeem")
    assert response.status_code == 200, response.text
    assert response.json()["workspace"]["viewer_role"] == "comment"
    start_thread(joining, workspace)


def test_share_modes(client, monkeypatch):
    monkeypatch.setattr(settings, "SHARING_MODES_ENABLED", ["readonly"])
    assert client.get("/shares/modes").json() == ["readonly"]
    monkeypatch.setattr(settings, "SHARING_MODES_ENABLED", ["comment", "readonly"])
    assert client.get("/shares/modes").json() == ["readonly", "comment"]


def test_the_user_has_a_github_id(client, user):
    assert client.get("/auth/user").json()["github_id"] == user.external_id


def test_links_never_lower_access(client, user, workspace, stranger, events, monkeypatch):
    monkeypatch.setattr(settings, "SHARING_MODES_ENABLED", ["readonly", "comment"])
    joining = share(workspace, user, stranger, AccessMode.READONLY)
    links = {
        mode: client.post(f"/workspaces/{workspace['id']}/shares", json={"mode": mode}).json()["url"].rsplit("/", 1)[-1]
        for mode in ["readonly", "comment"]
    }

    response = joining.post(f"/shares/{links['comment']}/redeem")
    assert response.json()["workspace"]["viewer_role"] == "comment"
    assert events[-1][0] == "collaborator.updated"

    response = joining.post(f"/shares/{links['readonly']}/redeem")
    assert response.json()["workspace"]["viewer_role"] == "comment"
