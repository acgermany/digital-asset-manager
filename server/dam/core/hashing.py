import hashlib
from typing import Callable, Optional

CHUNK = 1024 * 1024


def sha256_file(path: str, should_stop: Optional[Callable[[], bool]] = None) -> Optional[str]:
    """Streaming sha256. Returns None if should_stop() turns true mid-file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if should_stop and should_stop():
                return None
            h.update(chunk)
    return h.hexdigest()
