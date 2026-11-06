from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import SessionLocal
from app.models.user import IdentityProvider, User
from app.services.jwt_service import JWTService


@pytest.fixture(scope="module")
def viewer():
    with SessionLocal() as db:
        user = User(
            full_name="Max Mustermann",
            email="max@example.org",
            username="mmustermann",
            external_id="4343",
            identity_provider=IdentityProvider.github,
            access_token="not-a-token",
            token_expiry=datetime.now(UTC) + timedelta(days=30),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def as_user(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {JWTService.create_access_token(user)['access_token']}"}


def test_the_user_stream_tells_the_owner_about_new_workspaces(client):
    with client.websocket_connect("/events") as events:
        response = client.post("/workspaces", json={"title": "SR review", "pfs": ["SR"]})
        assert response.status_code == 201, response.text
        event = events.receive_json()

    assert event["type"] == "workspace.created"
    assert event["workspace_id"] == response.json()["id"]


def test_the_user_stream_follows_access_to_shared_workspaces(client, workspace, viewer):
    workspace_url = f"/workspaces/{workspace['id']}"
    share = client.post(f"{workspace_url}/shares", json={"mode": "readonly"}).json()
    redeem_url = f"/shares/{share['url'].rsplit('/', 1)[-1]}/redeem"

    with client.websocket_connect("/events", headers=as_user(viewer)) as events:
        response = client.post(redeem_url, headers=as_user(viewer))
        assert response.status_code == 200, response.text
        added = events.receive_json()

        client.patch(workspace_url, json={"title": "Renamed"})
        updated = events.receive_json()

        collaborator_id = response.json()["collaborator"]["id"]
        client.delete(f"{workspace_url}/collaborators/{collaborator_id}")
        revoked = events.receive_json()

        # Unlike the workspace's stream, the user's stays open
        client.post(redeem_url, headers=as_user(viewer))
        added_again = events.receive_json()

    assert added == added | {"type": "collaborator.added", "workspace_id": workspace["id"], "target_user_id": viewer.id, "mode": "readonly"}
    assert updated == updated | {"type": "workspace.updated", "workspace_id": workspace["id"], "fields": ["title"]}
    assert revoked == revoked | {"type": "collaborator.revoked", "workspace_id": workspace["id"], "target_user_id": viewer.id}
    assert added_again["type"] == "collaborator.added"


def test_inviting_tells_the_invitee_once(client, github, workspace, viewer):
    github.users[viewer.username] = {"id": int(viewer.external_id), "login": viewer.username, "type": "User"}
    workspace_url = f"/workspaces/{workspace['id']}"
    invite = {"github_usernames": [viewer.username], "mode": "readonly"}

    with client.websocket_connect("/events", headers=as_user(viewer)) as events:
        response = client.post(f"{workspace_url}/collaborators", json=invite)
        assert response.status_code < 300, response.text
        added = events.receive_json()

        # Inviting again with the same mode changes nothing, so the next event is the rename
        client.post(f"{workspace_url}/collaborators", json=invite)
        client.patch(workspace_url, json={"title": "Renamed"})
        renamed = events.receive_json()

    assert added == added | {"type": "collaborator.added", "workspace_id": workspace["id"], "target_user_id": viewer.id, "mode": "readonly"}
    assert renamed["type"] == "workspace.updated"
