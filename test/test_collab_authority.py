"""Tests for the collaborative-editing authority (app/services/collab_service.py).

For live sync the authority is text-agnostic - it orders and relays opaque update objects - so
those tests drive it with plain dict "updates" (no CodeMirror needed). Persistence is exercised
separately with a real git repo and a full-text snapshot.
"""

import asyncio
import json

import pygit2
import pytest

from app.services import collab_service
from app.services.collab_service import Connection, Room


@pytest.fixture(autouse=True)
def _clear_rooms():
    collab_service._rooms.clear()
    yield
    collab_service._rooms.clear()


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _conn(is_editor=True):
    ws = FakeWebSocket()
    return Connection(ws, is_editor), ws


def _room(tmp_path, base_doc=""):
    return Room("ws:/doc.md", base_doc, tmp_path, tmp_path / "doc.md")


def upd(client_id, tag):
    # A stand-in for a serialized @codemirror/collab update; the authority treats it as opaque.
    return {"clientID": client_id, "changes": tag}


def test_join_sends_init_with_base_doc_and_log(tmp_path):
    async def scenario():
        room = _room(tmp_path, "hello")
        room.updates.append(upd(1, "a"))
        conn, ws = _conn()
        await room.join(conn)
        assert ws.sent == [{"type": "init", "doc": "hello", "updates": [{"clientID": 1, "changes": "a"}]}]

    asyncio.run(scenario())


def test_matched_push_is_accepted_and_broadcast_to_everyone_including_sender(tmp_path):
    async def scenario():
        room = _room(tmp_path)
        a, a_ws = _conn()
        b, b_ws = _conn()
        room.connections.update({a, b})
        a_ws.sent.clear()
        b_ws.sent.clear()

        await room.handle(a, {"type": "push", "version": 0, "updates": [upd(1, "x")]})

        assert room.version == 1
        assert a_ws.sent == [{"type": "updates", "updates": [{"clientID": 1, "changes": "x"}]}]
        assert b_ws.sent == [{"type": "updates", "updates": [{"clientID": 1, "changes": "x"}]}]

    asyncio.run(scenario())


def test_stale_push_returns_missing_updates_and_is_not_applied(tmp_path):
    async def scenario():
        room = _room(tmp_path)
        room.updates.append(upd(1, "x"))  # version 1
        stale, stale_ws = _conn()
        other, other_ws = _conn()
        room.connections.update({stale, other})

        await room.handle(stale, {"type": "push", "version": 0, "updates": [upd(2, "y")]})

        assert room.version == 1  # stale update NOT applied
        assert stale_ws.sent == [{"type": "updates", "updates": [{"clientID": 1, "changes": "x"}]}]
        assert other_ws.sent == []  # not broadcast

    asyncio.run(scenario())


def test_future_push_triggers_resync(tmp_path):
    async def scenario():
        room = _room(tmp_path)
        conn, ws = _conn()
        room.connections.add(conn)
        await room.handle(conn, {"type": "push", "version": 5, "updates": [upd(1, "z")]})
        assert room.version == 0
        assert ws.sent == [{"type": "resync"}]

    asyncio.run(scenario())


def test_readonly_push_and_snapshot_are_dropped(tmp_path):
    async def scenario():
        room = _room(tmp_path, "base")
        viewer, viewer_ws = _conn(is_editor=False)
        editor, editor_ws = _conn(is_editor=True)
        room.connections.update({viewer, editor})

        await room.handle(viewer, {"type": "push", "version": 0, "updates": [upd(9, "hack")]})
        await room.handle(viewer, {"type": "snapshot", "doc": "HACKED"})

        assert room.version == 0
        assert room.current_doc == "base"  # viewer snapshot ignored
        assert viewer_ws.sent == []
        assert editor_ws.sent == []

    asyncio.run(scenario())


def test_concurrent_pushes_are_serialized_by_version(tmp_path):
    """A and B start at v0. A wins; B is told what it missed, rebases (test stand-in), and re-pushes
    at v1. Final log is ordered [A, B]."""

    async def scenario():
        room = _room(tmp_path)
        a, a_ws = _conn()
        b, b_ws = _conn()
        room.connections.update({a, b})

        await room.handle(a, {"type": "push", "version": 0, "updates": [upd(1, "A")]})
        assert room.version == 1

        b_ws.sent.clear()
        await room.handle(b, {"type": "push", "version": 0, "updates": [upd(2, "B")]})
        assert room.version == 1
        assert b_ws.sent == [{"type": "updates", "updates": [{"clientID": 1, "changes": "A"}]}]

        await room.handle(b, {"type": "push", "version": 1, "updates": [upd(2, "B'")]})
        assert room.version == 2
        assert room.updates == [{"clientID": 1, "changes": "A"}, {"clientID": 2, "changes": "B'"}]

    asyncio.run(scenario())


def test_editor_snapshot_is_persisted_to_git_when_last_client_leaves(tmp_path):
    """The core regression for 'a later reader sees the last-viewed state': the authority itself
    writes the latest snapshot to git when the room empties, so the on-disk file is current."""

    pygit2.init_repository(str(tmp_path))
    doc_file = tmp_path / "doc.md"
    doc_file.write_text("hello")

    async def scenario():
        room = collab_service.get_or_create_room("ws:/doc.md", "hello", tmp_path, doc_file)
        editor, _ = _conn(is_editor=True)
        room.connections.add(editor)

        await room.handle(editor, {"type": "snapshot", "doc": "hello world"})
        assert room.current_doc == "hello world"

        # Last client leaves -> flush to git, even though the 3s debounce hasn't fired.
        room.leave(editor)
        await collab_service.discard_room_if_empty("ws:/doc.md")

        assert doc_file.read_text() == "hello world"
        assert "ws:/doc.md" not in collab_service._rooms

    asyncio.run(scenario())
