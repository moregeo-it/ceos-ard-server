"""End-to-end test through the real FastAPI WebSocket route + authority + Starlette transport.

Only the DB-touching auth/authz step is stubbed (no OAuth / user row / DB needed); the route,
`collab_service`, init, push and broadcast run for real. Updates are opaque dicts (the authority
is text-agnostic), so no CodeMirror is required.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import collab as collab_api
from app.main import app
from app.services import collab_service


@pytest.fixture(autouse=True)
def _clear_rooms():
    collab_service._rooms.clear()
    yield
    collab_service._rooms.clear()


def test_editor_push_is_broadcast_to_other_client(tmp_path, monkeypatch):
    doc_file = tmp_path / "doc.md"
    doc_file.write_text("hello")

    def fake_authorize(token, workspace_id, file_path):
        normalized = file_path if file_path.startswith("/") else f"/{file_path}"
        return (token == "editor"), tmp_path, doc_file, normalized

    monkeypatch.setattr(collab_api, "_authorize", fake_authorize)

    client = TestClient(app)
    base = "/workspaces/ws1/collab/doc.md"
    with (
        client.websocket_connect(base + "?authorization=editor") as a,
        client.websocket_connect(base + "?authorization=editor") as b,
    ):
        # Both get init with the base doc.
        assert a.receive_json() == {"type": "init", "doc": "hello", "updates": []}
        assert b.receive_json() == {"type": "init", "doc": "hello", "updates": []}

        a.send_json({"type": "push", "version": 0, "updates": [{"clientID": 1, "changes": " world"}]})

        # Accepted and broadcast to BOTH (sender confirms its own by clientID, peer applies).
        assert a.receive_json() == {"type": "updates", "updates": [{"clientID": 1, "changes": " world"}]}
        assert b.receive_json() == {"type": "updates", "updates": [{"clientID": 1, "changes": " world"}]}


def test_readonly_client_receives_but_cannot_push(tmp_path, monkeypatch):
    doc_file = tmp_path / "doc.md"
    doc_file.write_text("base")

    def fake_authorize(token, workspace_id, file_path):
        normalized = file_path if file_path.startswith("/") else f"/{file_path}"
        return (token == "editor"), tmp_path, doc_file, normalized

    monkeypatch.setattr(collab_api, "_authorize", fake_authorize)

    client = TestClient(app)
    base = "/workspaces/ws1/collab/doc.md"
    with (
        client.websocket_connect(base + "?authorization=editor") as editor,
        client.websocket_connect(base + "?authorization=viewer") as viewer,
    ):
        editor.receive_json()  # init
        viewer.receive_json()  # init (viewers get content too)

        # Viewer's push is dropped: the editor sees the editor's own accepted push next, not the viewer's.
        viewer.send_json({"type": "push", "version": 0, "updates": [{"clientID": 9, "changes": "HACK"}]})
        editor.send_json({"type": "push", "version": 0, "updates": [{"clientID": 1, "changes": "ok"}]})

        assert editor.receive_json() == {"type": "updates", "updates": [{"clientID": 1, "changes": "ok"}]}


def test_unauthorized_connection_is_rejected(monkeypatch):
    def deny(token, workspace_id, file_path):
        raise HTTPException(status_code=403)

    monkeypatch.setattr(collab_api, "_authorize", deny)
    client = TestClient(app)

    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/workspaces/ws1/collab/doc.md?authorization=nope") as ws:
            ws.receive_json()
