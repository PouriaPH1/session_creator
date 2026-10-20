"""
chat_service.py — Real-time Telegram Chat Viewer Service

Provides:
  - Fetching dialogs (DMs, groups, channels) for a given session
  - Fetching message history for a specific peer
  - Sending messages
  - Real-time new-message streaming via asyncio.Queue (consumed by WebSocket handler)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from telethon import TelegramClient, events
from telethon.tl.types import (
    User, Chat, Channel,
    MessageMediaPhoto, MessageMediaDocument,
    MessageMediaWebPage,
    PeerUser, PeerChat, PeerChannel,
)
from telethon.errors import FloodWaitError, ChatWriteForbiddenError

from server.session_pool import TelethonSessionPool

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _entity_type(entity) -> str:
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "group"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


def _avatar_color(name: str) -> str:
    """Deterministic color class from name (for letter avatars)."""
    colors = [
        "bg-red-500", "bg-orange-500", "bg-amber-500", "bg-yellow-500",
        "bg-lime-600", "bg-green-500", "bg-teal-500", "bg-cyan-500",
        "bg-sky-500", "bg-blue-500", "bg-indigo-500", "bg-violet-500",
        "bg-purple-500", "bg-fuchsia-500", "bg-pink-500", "bg-rose-500",
    ]
    idx = sum(ord(c) for c in (name or "?")) % len(colors)
    return colors[idx]


def _format_ts(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _media_type(msg) -> Optional[str]:
    if msg.media is None:
        return None
    if isinstance(msg.media, MessageMediaPhoto):
        return "photo"
    if isinstance(msg.media, MessageMediaDocument):
        doc = msg.media.document
        if doc:
            for attr in getattr(doc, "attributes", []):
                cls_name = type(attr).__name__
                if "Video" in cls_name:
                    return "video"
                if "Audio" in cls_name:
                    return "audio"
                if "Voice" in cls_name:
                    return "voice"
                if "Sticker" in cls_name:
                    return "sticker"
        return "document"
    if isinstance(msg.media, MessageMediaWebPage):
        return "webpage"
    return "other"


def _serialize_message(msg, me_id: int) -> Dict[str, Any]:
    """Convert a Telethon Message object to a JSON-serializable dict."""
    sender_id = None
    sender_name = None

    if msg.sender:
        sender_id = msg.sender.id
        if isinstance(msg.sender, User):
            sender_name = " ".join(
                filter(None, [msg.sender.first_name, msg.sender.last_name])
            ) or msg.sender.username or str(msg.sender.id)
        else:
            sender_name = getattr(msg.sender, "title", None) or str(msg.sender.id)
    elif msg.from_id:
        peer = msg.from_id
        if isinstance(peer, PeerUser):
            sender_id = peer.user_id
        elif isinstance(peer, (PeerChat, PeerChannel)):
            sender_id = getattr(peer, "chat_id", None) or getattr(peer, "channel_id", None)

    is_outgoing = msg.out or (sender_id == me_id)

    reply_to_id = None
    if msg.reply_to and hasattr(msg.reply_to, "reply_to_msg_id"):
        reply_to_id = msg.reply_to.reply_to_msg_id

    return {
        "id": msg.id,
        "text": msg.text or "",
        "date": _format_ts(msg.date),
        "is_outgoing": is_outgoing,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "media_type": _media_type(msg),
        "reply_to_id": reply_to_id,
        "views": getattr(msg, "views", None),
        "forwarded": msg.fwd_from is not None,
    }


def _serialize_dialog(dialog, me_id: int) -> Dict[str, Any]:
    """Convert a Telethon Dialog to a JSON-serializable dict for the sidebar."""
    entity = dialog.entity
    etype = _entity_type(entity)

    if isinstance(entity, User):
        name = " ".join(
            filter(None, [entity.first_name, entity.last_name])
        ) or entity.username or str(entity.id)
        peer_id = str(entity.id)
    else:
        name = getattr(entity, "title", str(entity.id))
        peer_id = str(getattr(entity, "id", 0))

    last_msg = dialog.message
    last_text = ""
    last_date = None
    last_is_outgoing = False

    if last_msg:
        last_text = last_msg.text or ("[رسانه]" if last_msg.media else "")
        last_date = _format_ts(last_msg.date)
        last_is_outgoing = last_msg.out or (
            getattr(last_msg, "from_id", None) is not None
            and isinstance(last_msg.from_id, PeerUser)
            and last_msg.from_id.user_id == me_id
        )

    return {
        "id": peer_id,
        "name": name,
        "type": etype,
        "unread_count": dialog.unread_count,
        "last_message": last_text[:80],
        "last_date": last_date,
        "last_is_outgoing": last_is_outgoing,
        "avatar_color": _avatar_color(name),
        "avatar_letter": (name[0].upper() if name else "?"),
        "username": getattr(entity, "username", None),
        "is_bot": getattr(entity, "bot", False) if isinstance(entity, User) else False,
        "members_count": getattr(entity, "participants_count", None),
    }


# ──────────────────────────────────────────────────────────────────────────────
# ChatService
# ──────────────────────────────────────────────────────────────────────────────

class ChatService:
    """
    Wraps Telethon chat operations for the admin panel's chat viewer.

    One ChatService is shared across the whole app.
    Active WebSocket listeners are tracked per (identifier, peer_id) so we
    can route incoming messages to the correct browser tab.
    """

    def __init__(self, pool: TelethonSessionPool):
        self.pool = pool
        # {identifier: {peer_id: asyncio.Queue}}
        self._listeners: Dict[str, Dict[str, asyncio.Queue]] = {}
        # {identifier: registered}  — tracks which clients have a global handler
        self._handlers: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Dialogs (sidebar)
    # ------------------------------------------------------------------

    async def get_dialogs(
        self, identifier: str, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        client = self._get_client(identifier)
        me = await client.get_me()
        me_id = me.id

        dialogs = await client.get_dialogs(limit=limit, offset_id=offset)
        return [_serialize_dialog(d, me_id) for d in dialogs]

    # ------------------------------------------------------------------
    # Message history
    # ------------------------------------------------------------------

    async def get_messages(
        self, identifier: str, peer: str, limit: int = 50, offset_id: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Returns (messages_oldest_first, me_id).
        messages are sorted oldest-first so the browser can append them top-to-bottom.
        """
        client = self._get_client(identifier)
        me = await client.get_me()
        me_id = me.id

        entity = await client.get_entity(self._parse_peer(peer))
        msgs = await client.get_messages(entity, limit=limit, offset_id=offset_id)

        serialized = [_serialize_message(m, me_id) for m in reversed(msgs)]
        return serialized, me_id

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------

    async def send_message(
        self, identifier: str, peer: str, text: str
    ) -> Dict[str, Any]:
        client = self._get_client(identifier)
        me = await client.get_me()

        try:
            entity = await client.get_entity(self._parse_peer(peer))
            sent = await client.send_message(entity, text)
            return _serialize_message(sent, me.id)
        except FloodWaitError as e:
            raise RuntimeError(f"Flood wait: باید {e.seconds} ثانیه صبر کنید.") from e
        except ChatWriteForbiddenError:
            raise RuntimeError("ارسال پیام در این چت مجاز نیست.") from None
        except Exception as e:
            raise RuntimeError(str(e)) from e

    # ------------------------------------------------------------------
    # Real-time listener (WebSocket)
    # ------------------------------------------------------------------

    def register_listener(self, identifier: str, peer_id: str) -> asyncio.Queue:
        """
        Register a queue that will receive new messages for (identifier, peer_id).
        Also ensures a global event handler is installed on the Telethon client.
        """
        if identifier not in self._listeners:
            self._listeners[identifier] = {}
        if peer_id not in self._listeners[identifier]:
            self._listeners[identifier][peer_id] = asyncio.Queue(maxsize=200)

        self._ensure_handler(identifier)
        return self._listeners[identifier][peer_id]

    def unregister_listener(self, identifier: str, peer_id: str) -> None:
        if identifier in self._listeners:
            self._listeners[identifier].pop(peer_id, None)

    def _ensure_handler(self, identifier: str) -> None:
        """Install a new-message event handler on the client (once per client)."""
        if self._handlers.get(identifier):
            return
        client = self.pool.get_client(identifier)
        if not client:
            return

        @client.on(events.NewMessage())
        async def _on_new_message(event):
            await self._dispatch_message(identifier, event)

        self._handlers[identifier] = True
        logger.info(f"[ChatService] Event handler registered for session '{identifier}'")

    async def _dispatch_message(self, identifier: str, event) -> None:
        """Route an incoming message to every listening queue for its peer."""
        msg = event.message
        if not msg:
            return

        # Determine which peer_id this message belongs to
        peer_id = self._peer_id_from_message(msg)

        listeners = self._listeners.get(identifier, {})

        # Notify the specific peer listener AND the "all" listener (for dialog updates)
        for key in (peer_id, "__all__"):
            q = listeners.get(key)
            if q:
                try:
                    # We need me_id to mark outgoing correctly; grab it cheaply
                    client = self.pool.get_client(identifier)
                    me = await client.get_me()
                    serialized = _serialize_message(msg, me.id)
                    serialized["_peer_id"] = peer_id  # extra field for "__all__" listener
                    q.put_nowait(serialized)
                except asyncio.QueueFull:
                    logger.warning(f"[ChatService] Queue full for {identifier}/{key}")
                except Exception as e:
                    logger.error(f"[ChatService] Dispatch error: {e}")

    # ------------------------------------------------------------------
    # Peer info
    # ------------------------------------------------------------------

    async def get_peer_info(self, identifier: str, peer: str) -> Dict[str, Any]:
        client = self._get_client(identifier)
        entity = await client.get_entity(self._parse_peer(peer))
        etype = _entity_type(entity)

        if isinstance(entity, User):
            name = " ".join(
                filter(None, [entity.first_name, entity.last_name])
            ) or entity.username or str(entity.id)
        else:
            name = getattr(entity, "title", str(entity.id))

        return {
            "id": str(entity.id),
            "name": name,
            "type": etype,
            "username": getattr(entity, "username", None),
            "avatar_color": _avatar_color(name),
            "avatar_letter": (name[0].upper() if name else "?"),
            "is_bot": getattr(entity, "bot", False) if isinstance(entity, User) else False,
            "members_count": getattr(entity, "participants_count", None),
            "description": getattr(entity, "about", None),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self, identifier: str) -> TelegramClient:
        client = self.pool.get_client(identifier)
        if not client:
            raise ValueError(f"سشن '{identifier}' یافت نشد یا فعال نیست.")
        return client

    @staticmethod
    def _parse_peer(peer: str):
        """
        Try to parse peer as an integer (user_id / chat_id) first,
        otherwise return as-is (username or phone).
        """
        peer = peer.strip()
        try:
            return int(peer)
        except ValueError:
            return peer

    @staticmethod
    def _peer_id_from_message(msg) -> str:
        """Extract the canonical peer_id string from a message."""
        peer = msg.peer_id
        if isinstance(peer, PeerUser):
            return str(peer.user_id)
        if isinstance(peer, PeerChat):
            return str(peer.chat_id)
        if isinstance(peer, PeerChannel):
            return str(peer.channel_id)
        return "0"
