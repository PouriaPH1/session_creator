import struct
import pytest
from telethon.extensions import BinaryReader
from telethon.tl.alltlobjects import tlobjects
from telethon.tl.types import User
from telethon.tl.types.messages import Messages, MessagesSlice, ChannelMessages

from core.telethon_patch import apply_telethon_patches


def test_telethon_patch_registration():
    """Verify that constructor 0x31774388 is registered in tlobjects."""
    apply_telethon_patches()
    assert 0x31774388 in tlobjects
    assert tlobjects[0x31774388] is User


def test_messages_resilient_to_unknown_user_constructor():
    """Verify that unknown constructors in the users vector do not raise TypeNotFoundError."""
    apply_telethon_patches()

    buf = bytearray()
    # messages: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 1)
    # MessageEmpty: flags=0, id=999
    buf += struct.pack('<III', 0x90a6ca84, 0, 999)
    # topics: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 0)
    # chats: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 0)
    # users: vector with 1 unknown constructor
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 1)
    buf += struct.pack('<I', 0x76543210)  # Unknown constructor ID

    reader = BinaryReader(bytes(buf))
    result = Messages.from_reader(reader)

    assert len(result.messages) == 1
    assert result.messages[0].id == 999
    # users list should safely stop and not crash
    assert len(result.users) == 0


def test_messages_slice_resilient_to_unknown_user_constructor():
    """Verify that MessagesSlice is resilient against unknown constructors in users."""
    apply_telethon_patches()

    buf = bytearray()
    # flags=0, count=1
    buf += struct.pack('<II', 0, 1)
    # messages: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 1)
    buf += struct.pack('<III', 0x90a6ca84, 0, 888)
    # topics: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 0)
    # chats: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 0)
    # users: vector with unknown constructor
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 1)
    buf += struct.pack('<I', 0x54321098)

    reader = BinaryReader(bytes(buf))
    result = MessagesSlice.from_reader(reader)

    assert len(result.messages) == 1
    assert result.messages[0].id == 888
    assert len(result.users) == 0


def test_channel_messages_resilient_to_unknown_user_constructor():
    """Verify that ChannelMessages is resilient against unknown constructors in users."""
    apply_telethon_patches()

    buf = bytearray()
    # flags=0, pts=100, count=1
    buf += struct.pack('<III', 0, 100, 1)
    # messages: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 1)
    buf += struct.pack('<III', 0x90a6ca84, 0, 777)
    # topics: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 0)
    # chats: vector
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 0)
    # users: vector with unknown constructor
    buf += struct.pack('<I', 0x1cb5c415)
    buf += struct.pack('<I', 1)
    buf += struct.pack('<I', 0x43210987)

    reader = BinaryReader(bytes(buf))
    result = ChannelMessages.from_reader(reader)

    assert len(result.messages) == 1
    assert result.messages[0].id == 777
    assert len(result.users) == 0
