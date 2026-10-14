import socket
from core.port_selector import find_available_port, is_port_in_use


def test_is_port_in_use_on_free_port():
    # Find a free ephemeral port from OS
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    # Socket is now closed, so it should not be in use
    assert is_port_in_use(free_port, host="127.0.0.1") is False


def test_is_port_in_use_on_bound_port():
    # Bind a port and keep it open
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    occupied_port = s.getsockname()[1]
    try:
        assert is_port_in_use(occupied_port, host="127.0.0.1") is True
    finally:
        s.close()


def test_find_available_port_when_occupied():
    # Occupy a port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    occupied_port = s.getsockname()[1]
    try:
        port, was_changed = find_available_port(preferred_port=occupied_port, host="127.0.0.1")
        assert was_changed is True
        assert port != occupied_port
        assert is_port_in_use(port, host="127.0.0.1") is False
    finally:
        s.close()
