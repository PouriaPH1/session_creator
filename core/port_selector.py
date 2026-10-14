import socket
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    """
    Checks if a network port is currently occupied by another service or process.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return False
        except OSError:
            return True


def find_available_port(
    preferred_port: int = 8000,
    host: str = "0.0.0.0",
    max_scan_range: int = 200
) -> Tuple[int, bool]:
    """
    Verifies if preferred_port is available. If occupied, sequentially searches
    for the next available free port.

    Returns:
        (port: int, was_changed: bool)
    """
    # Check preferred port first
    if not is_port_in_use(preferred_port, host):
        return preferred_port, False

    # Search next available ports
    for port in range(preferred_port + 1, preferred_port + max_scan_range + 1):
        if not is_port_in_use(port, host):
            return port, True

    # Fallback to an arbitrary free port assigned by the OS
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        fallback_port = s.getsockname()[1]
        return fallback_port, True
