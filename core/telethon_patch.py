"""
Resilience patches for Telethon TL deserialization and MTProto updates.

Resolves issues where Telegram servers return MTProto objects (such as channels, users,
or messages) using constructor IDs not yet defined or deprecated in the installed Telethon layer
(e.g., Constructor ID 1c32b11c / 0x1c32b11c for Channel, 31774388 / 0x31774388 for User).
"""

import logging

logger = logging.getLogger(__name__)

_PATCHED = False


def apply_telethon_patches():
    """
    Applies resilience patches to Telethon:
    1. Registers known missing constructor IDs:
       - 0x1c32b11c -> Channel
       - 0x31774388 -> User
       - 0xe4e0b29d -> ChannelFull
       - 0x95ef6f2b -> Message
       - 0xe6c31522 -> MessageActionStarGiftUnique
    2. Makes message collections (Messages, MessagesSlice, ChannelMessages) resilient
       against unknown constructors in the trailing `users` vector.
    3. Makes MTProtoSender._handle_gzip_packed resilient against unparseable background updates.
    """
    global _PATCHED
    if _PATCHED:
        return

    try:
        from telethon.tl.alltlobjects import tlobjects
        import telethon.tl.types as tl_types
        from telethon.tl.types.messages import Messages, MessagesSlice, ChannelMessages
        from telethon.network.mtprotosender import MTProtoSender

        # 1. Register missing constructor IDs in Telethon's global tlobjects table
        missing_constructors = {
            0x31774388: ("User", "user#31774388"),
            0x1c32b11c: ("Channel", "channel#1c32b11c"),
            0xe4e0b29d: ("ChannelFull", "channelFull#e4e0b29d"),
            0x95ef6f2b: ("Message", "message#95ef6f2b"),
            0xe6c31522: ("MessageActionStarGiftUnique", "messageActionStarGiftUnique#e6c31522"),
        }

        for cid, (cls_name, desc) in missing_constructors.items():
            if cid not in tlobjects:
                target_cls = getattr(tl_types, cls_name, None)
                if target_cls:
                    tlobjects[cid] = target_cls
                    logger.debug("Registered missing TL constructor %s (%s) -> %s", hex(cid), desc, cls_name)

        # 2. Resilient parsing for Messages types
        def _make_resilient_messages_reader():
            orig_from_reader = Messages.from_reader
            if getattr(orig_from_reader, "_is_resilient", False):
                return

            @classmethod
            def resilient_messages_from_reader(cls, reader):
                reader.read_int()
                _messages = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _messages.append(_x)

                reader.read_int()
                _topics = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _topics.append(_x)

                reader.read_int()
                _chats = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _chats.append(_x)

                _users = []
                try:
                    reader.read_int()
                    count = reader.read_int()
                    for _ in range(count):
                        try:
                            _x = reader.tgread_object()
                            _users.append(_x)
                        except Exception as ex:
                            logger.debug("Resiliently bypassed unparseable user object: %s", ex)
                            break
                except Exception as ex:
                    logger.debug("Resiliently caught users vector exception: %s", ex)

                return cls(messages=_messages, topics=_topics, chats=_chats, users=_users)

            resilient_messages_from_reader._is_resilient = True
            Messages.from_reader = resilient_messages_from_reader

        def _make_resilient_slice_reader():
            orig_from_reader = MessagesSlice.from_reader
            if getattr(orig_from_reader, "_is_resilient", False):
                return

            @classmethod
            def resilient_slice_from_reader(cls, reader):
                flags = reader.read_int()
                _inexact = bool(flags & 2)
                _count = reader.read_int()
                _next_rate = reader.read_int() if (flags & 1) else None
                _offset_id_offset = reader.read_int() if (flags & 4) else None
                _search_flood = reader.tgread_object() if (flags & 8) else None

                reader.read_int()
                _messages = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _messages.append(_x)

                reader.read_int()
                _topics = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _topics.append(_x)

                reader.read_int()
                _chats = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _chats.append(_x)

                _users = []
                try:
                    reader.read_int()
                    count = reader.read_int()
                    for _ in range(count):
                        try:
                            _x = reader.tgread_object()
                            _users.append(_x)
                        except Exception as ex:
                            logger.debug("Resiliently bypassed unparseable user object in MessagesSlice: %s", ex)
                            break
                except Exception as ex:
                    logger.debug("Resiliently caught users vector exception in MessagesSlice: %s", ex)

                return cls(
                    count=_count,
                    messages=_messages,
                    topics=_topics,
                    chats=_chats,
                    users=_users,
                    inexact=_inexact,
                    next_rate=_next_rate,
                    offset_id_offset=_offset_id_offset,
                    search_flood=_search_flood,
                )

            resilient_slice_from_reader._is_resilient = True
            MessagesSlice.from_reader = resilient_slice_from_reader

        def _make_resilient_channel_messages_reader():
            orig_from_reader = ChannelMessages.from_reader
            if getattr(orig_from_reader, "_is_resilient", False):
                return

            @classmethod
            def resilient_channel_from_reader(cls, reader):
                flags = reader.read_int()
                _inexact = bool(flags & 2)
                _pts = reader.read_int()
                _count = reader.read_int()
                _offset_id_offset = reader.read_int() if (flags & 4) else None

                reader.read_int()
                _messages = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _messages.append(_x)

                reader.read_int()
                _topics = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _topics.append(_x)

                reader.read_int()
                _chats = []
                for _ in range(reader.read_int()):
                    _x = reader.tgread_object()
                    _chats.append(_x)

                _users = []
                try:
                    reader.read_int()
                    count = reader.read_int()
                    for _ in range(count):
                        try:
                            _x = reader.tgread_object()
                            _users.append(_x)
                        except Exception as ex:
                            logger.debug("Resiliently bypassed unparseable user object in ChannelMessages: %s", ex)
                            break
                except Exception as ex:
                    logger.debug("Resiliently caught users vector exception in ChannelMessages: %s", ex)

                return cls(
                    pts=_pts,
                    count=_count,
                    messages=_messages,
                    topics=_topics,
                    chats=_chats,
                    users=_users,
                    inexact=_inexact,
                    offset_id_offset=_offset_id_offset,
                )

            resilient_channel_from_reader._is_resilient = True
            ChannelMessages.from_reader = resilient_channel_from_reader

        def _make_resilient_mtproto_sender():
            orig_handle_gzip = MTProtoSender._handle_gzip_packed
            if getattr(orig_handle_gzip, "_is_resilient", False):
                return

            async def resilient_handle_gzip(self, message):
                try:
                    await orig_handle_gzip(self, message)
                except Exception as e:
                    self._log.warning("Resiliently bypassed unparseable background gzipped update: %s", e)

            resilient_handle_gzip._is_resilient = True
            MTProtoSender._handle_gzip_packed = resilient_handle_gzip

        _make_resilient_messages_reader()
        _make_resilient_slice_reader()
        _make_resilient_channel_messages_reader()
        _make_resilient_mtproto_sender()

        _PATCHED = True
        logger.info("Successfully applied Telethon resilience patches.")
    except Exception as e:
        logger.warning("Failed to apply some Telethon resilience patches: %s", e)


# Auto-apply on import
apply_telethon_patches()
