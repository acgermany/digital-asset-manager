"""Keep-candidate scoring for duplicate groups (higher = better copy to keep)."""
import os
import re

BAD_FOLDERS = ("unknown", "#recycle", "unorganized", "unsorted", "temp", "tmp")
COPY_PATTERNS = (r"\s*\(\d+\)$", r"\s+\d+\.$", r"_\d+$", r" copy$", r"_copy$")


def score(path: str, filename: str) -> float:
    s = 0.0
    lower = path.lower()
    stem = os.path.splitext(filename)[0].lower()
    for bad in BAD_FOLDERS:
        if f"/{bad}/" in lower or lower.endswith(f"/{bad}"):
            s -= 50
    for pattern in COPY_PATTERNS:
        if re.search(pattern, stem):
            s -= 100
    s -= len(path) * 0.01  # shorter path = better organised
    return round(s, 2)
