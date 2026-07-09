"""In-memory collaborative-editing authority (single process).

Implements the model from CodeMirror's collab example (https://codemirror.net/examples/collab/):
operational transformation with a central authority. For live sync the server is text-agnostic -
it assigns each client edit a sequential version and relays edits in order; all OT rebasing happens
in the browser inside `@codemirror/collab`.

Per file we keep a `Room` with:
  - `base_doc`:  the git text read when the room was created == version 0
  - `updates`:   the ordered list of client updates since v0 (opaque JSON: {clientID, changes})
  - `version` == len(updates)
  - `current_doc`: the latest full-text snapshot a client sent (used only for persistence)

Protocol (JSON over one WebSocket per open file):
  server -> client  {"type": "init",     "doc": <base_doc>, "updates": [...all...]}
  client -> server  {"type": "push",     "version": <int>,  "updates": [...]}
  server -> client  {"type": "updates",  "updates": [...]}      (accepted edits, to everyone)
  server -> client  {"type": "resync"}                          (client is ahead of a reset room)
  client -> server  {"type": "snapshot", "doc": <full text>}    (for durable persistence)

A stale push is answered with the missing updates so `@codemirror/collab` can rebase and re-push.
Accepted updates are broadcast to *everyone* including the sender - CodeMirror recognizes the
sender's own updates by clientID and simply confirms them.

**Persistence is owned by the authority** (not the client): editors periodically send a full-text
snapshot, and the room writes+stages it to git on a debounce and, crucially, when the LAST client
leaves. This is what makes a later reader (who opens the file after everyone left) see the latest
content instead of a stale on-disk copy. Rooms are in-memory and per-process (fine for the
single-uvicorn deployment; multi-worker would need a shared store).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import WebSocket

from app.utils.git_utils import get_repo

logger = logging.getLogger(__name__)

PERSIST_DEBOUNCE_SECONDS = 3

# Process-wide room registry, keyed by room_key(workspace_id, file_path).
_rooms: dict[str, "Room"] = {}


def room_key(workspace_id: str, file_path: str) -> str:
    return f"{workspace_id}:{file_path}"


class Connection:
    """One connected client. Sends are serialized so concurrent broadcasts never interleave."""

    def __init__(self, websocket: WebSocket, is_editor: bool):
        self.websocket = websocket
        self.is_editor = is_editor
        self._send_lock = asyncio.Lock()

    async def send(self, message: dict) -> None:
        async with self._send_lock:
            await self.websocket.send_text(json.dumps(message))


class Room:
    """The authority for one file: a base document at version 0 plus an ordered update log."""

    def __init__(self, key: str, base_doc: str, workspace_abs_path: Path, abs_file_path: Path):
        self.key = key
        self.base_doc = base_doc
        self.updates: list[dict] = []
        self.connections: set[Connection] = set()
        self.workspace_abs_path = workspace_abs_path
        self.abs_file_path = abs_file_path
        # Latest full-text snapshot from a client, and what we last wrote to disk.
        self.current_doc = base_doc
        self._last_persisted = base_doc
        self._persist_task: asyncio.Task | None = None

    @property
    def version(self) -> int:
        return len(self.updates)

    async def join(self, conn: Connection) -> None:
        self.connections.add(conn)
        # The client starts at version 0 with base_doc and replays these updates to catch up.
        await conn.send({"type": "init", "doc": self.base_doc, "updates": self.updates})

    def leave(self, conn: Connection) -> None:
        self.connections.discard(conn)

    async def handle(self, conn: Connection, message: dict) -> None:
        msg_type = message.get("type")

        if msg_type == "snapshot":
            # Only editors can change the document, so only their snapshots are trusted.
            if conn.is_editor and isinstance(message.get("doc"), str):
                self.current_doc = message["doc"]
                self._schedule_persist()
            return

        if msg_type != "push":
            return
        # Server-side read-only enforcement: a non-editor's edits are dropped (the UI is also
        # read-only, but the server must not trust that).
        if not conn.is_editor:
            return

        version = message.get("version")
        updates = message.get("updates") or []
        if not isinstance(version, int):
            return

        if version > self.version:
            # The client is ahead of us - only possible if this room was reset underneath it.
            # Tell it to reload the file from scratch.
            await conn.send({"type": "resync"})
            return
        if version < self.version:
            # Stale push: hand back the updates the client is missing so `@codemirror/collab`
            # can rebase its pending edits and re-push. Do NOT apply the stale updates.
            await conn.send({"type": "updates", "updates": self.updates[version:]})
            return

        # In sync: accept and broadcast to everyone (incl. sender, who confirms its own by clientID).
        self.updates.extend(updates)
        await self._broadcast({"type": "updates", "updates": updates})

    async def _broadcast(self, message: dict) -> None:
        for conn in list(self.connections):
            try:
                await conn.send(message)
            except Exception:
                # A dead/slow peer must not block delivery to others; its own serve loop cleans up.
                logger.debug("Dropping collab broadcast to a failed peer in room %s", self.key)

    # --- persistence ------------------------------------------------------

    def _schedule_persist(self) -> None:
        if self._persist_task is not None and not self._persist_task.done():
            self._persist_task.cancel()
        self._persist_task = asyncio.create_task(self._debounced_persist())

    async def _debounced_persist(self) -> None:
        try:
            await asyncio.sleep(PERSIST_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        await self.persist()

    async def persist(self) -> None:
        """Write the latest snapshot to disk and stage it. Called on debounce and on last-leave."""
        if self._persist_task is not None and not self._persist_task.done():
            self._persist_task.cancel()
        if self.current_doc == self._last_persisted:
            return
        try:
            # Runs inline on the event loop so it serializes with the REST write path and can't
            # corrupt the shared per-workspace pygit2 index.
            repo = get_repo(self.workspace_abs_path)
            self.abs_file_path.write_bytes(self.current_doc.encode("utf-8"))
            relative_path = str(self.abs_file_path.relative_to(self.workspace_abs_path)).replace("\\", "/")
            repo.index.add(relative_path)
            repo.index.write()
            self._last_persisted = self.current_doc
        except Exception:
            logger.exception("Failed to persist collaborative buffer for room %s", self.key)


def get_or_create_room(key: str, base_doc: str, workspace_abs_path: Path, abs_file_path: Path) -> Room:
    # Synchronous and await-free: on a single-threaded event loop this completes before any
    # concurrent connection coroutine resumes, so there is no create/create race.
    room = _rooms.get(key)
    if room is None:
        room = Room(key, base_doc, workspace_abs_path, abs_file_path)
        _rooms[key] = room
    return room


async def discard_room_if_empty(key: str) -> None:
    """If the room has no more clients, flush its latest snapshot to git and drop it."""
    room = _rooms.get(key)
    if room is not None and not room.connections:
        await room.persist()
        _rooms.pop(key, None)
