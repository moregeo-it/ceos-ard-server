import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from pycrdt import Text, YMessageType, YSyncMessageType
from pycrdt.websocket import WebsocketServer
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.services.file_service import FileService

logger = logging.getLogger(__name__)

FLUSH_DEBOUNCE_SECONDS = 10
CONTENT_KEY = "content"

# Single process-wide room registry. Rooms are ephemeral (in-memory only) and keyed by
# "{workspace_id}:{file_path}" - fine for the current single-uvicorn-process deployment;
# a multi-worker/multi-instance deployment would need to move this to a shared broker.
ws_server = WebsocketServer(rooms_ready=True, auto_clean_rooms=False)

_file_service = FileService()


@dataclass
class RoomMeta:
    workspace_abs_path: Path
    abs_file_path: Path
    last_persisted: str
    flush_task: asyncio.Task | None = None
    subscription: object = None


_room_meta: dict[str, RoomMeta] = {}


def room_key(workspace_id: str, file_path: str) -> str:
    return f"{workspace_id}:{file_path}"


class FastAPIWebsocketChannel:
    """Adapts a Starlette WebSocket to the pycrdt `Channel` protocol."""

    def __init__(self, websocket: WebSocket, path: str):
        self._websocket = websocket
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._websocket.receive_bytes()
        except WebSocketDisconnect:
            raise StopAsyncIteration()

    async def send(self, message: bytes) -> None:
        await self._websocket.send_bytes(message)

    async def recv(self) -> bytes:
        return await self.__anext__()


class RoleGatedChannel:
    """Wraps a Channel and silently drops mutating sync messages from non-editor clients.

    The frontend already renders readonly/comment editors as non-editable, but that's a UI
    restriction only - the server must not trust it. SYNC_STEP1 and AWARENESS messages are
    always allowed through (they never mutate the shared document); SYNC_STEP2/SYNC_UPDATE
    from a non-editor connection are dropped instead of being applied to the room's document.
    """

    def __init__(self, inner, is_editor: bool):
        self._inner = inner
        self.is_editor = is_editor

    @property
    def path(self) -> str:
        return self._inner.path

    async def send(self, message: bytes) -> None:
        await self._inner.send(message)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        while True:
            message = await self._inner.__anext__()
            if (
                not self.is_editor
                and len(message) > 1
                and message[0] == YMessageType.SYNC
                and message[1] in (YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE)
            ):
                continue
            return message


async def serve(
    workspace_id: str,
    file_path: str,
    workspace_abs_path: Path,
    abs_file_path: Path,
    channel,
    is_editor: bool,
) -> None:
    """Join (creating if needed) the collab room for a file and serve one client connection.

    Blocks for the lifetime of the connection. On the last participant leaving the room,
    flushes any unpersisted content to disk/git as a safety net.
    """
    key = room_key(workspace_id, file_path)
    is_new_room = key not in ws_server.rooms
    room = await ws_server.get_room(key)

    if is_new_room:
        seed = abs_file_path.read_text(encoding="utf-8", errors="replace") if abs_file_path.exists() else ""
        text = room.ydoc.get(CONTENT_KEY, type=Text)
        if seed:
            text.insert(0, seed)
        meta = RoomMeta(workspace_abs_path=workspace_abs_path, abs_file_path=abs_file_path, last_persisted=seed)
        meta.subscription = room.ydoc.observe(lambda event, k=key: _on_doc_change(k))
        _room_meta[key] = meta

    gated_channel = RoleGatedChannel(channel, is_editor=is_editor)
    try:
        await room.serve(gated_channel)
    finally:
        if not room.clients:
            await _flush(key)
            meta = _room_meta.pop(key, None)
            if meta:
                if meta.flush_task:
                    meta.flush_task.cancel()
                if meta.subscription is not None:
                    room.ydoc.unobserve(meta.subscription)
            if key in ws_server.rooms:
                await ws_server.delete_room(name=key)


def _on_doc_change(key: str) -> None:
    meta = _room_meta.get(key)
    if meta is None:
        return
    if meta.flush_task is not None and not meta.flush_task.done():
        meta.flush_task.cancel()
    meta.flush_task = asyncio.create_task(_debounced_flush(key))


async def _debounced_flush(key: str) -> None:
    try:
        await asyncio.sleep(FLUSH_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return
    await _flush(key)


async def _flush(key: str) -> None:
    meta = _room_meta.get(key)
    room = ws_server.rooms.get(key)
    if meta is None or room is None:
        return
    content = str(room.ydoc.get(CONTENT_KEY, type=Text))
    if content == meta.last_persisted:
        return
    try:
        _file_service.write_and_stage(meta.workspace_abs_path, meta.abs_file_path, content.encode("utf-8"))
        meta.last_persisted = content
    except Exception:
        logger.exception("Failed to flush collaborative buffer for room %s", key)
