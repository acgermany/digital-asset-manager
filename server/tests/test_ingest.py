import os
import time

import pytest

from dam import queue
from dam.core import settings_store
from dam.core.ingest import Deferred, process, register
from dam.models import Asset, File, Ingest, Review
from dam.watcher import scan_once


def write(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def ingest_file(session, hot, path):
    st = os.stat(path)
    i = register(session, hot, path, st.st_size, st.st_mtime)
    session.commit()
    return i


def cfg(hot, lib, **extra):
    return {"hotfolder": hot, "library": lib, "delete_mode": "delete", **extra}


def test_new_file_is_filed_into_library_with_provenance(session, dirs):
    hot, lib = dirs
    p = write(os.path.join(hot, "Grandma", "photo.jpg"), b"one")
    i = ingest_file(session, hot, p)
    assert i.provenance == "Grandma"
    process(session, i, cfg(hot, lib))
    assert i.status == "accepted" and i.decision == "new"
    assert not os.path.exists(p)
    assert os.path.isfile(i.current_path) and i.current_path.startswith(os.path.join(lib, "Grandma"))
    asset = session.get(Asset, i.asset_id)
    assert asset.sha256 and asset.files[0].source == "library"
    assert "photo.jpg" in asset.search_text


def test_exact_duplicate_same_name_ignoring_case_is_deleted(session, dirs):
    hot, lib = dirs
    first = ingest_file(session, hot, write(os.path.join(hot, "a", "Clip.MOV"), b"same"))
    process(session, first, cfg(hot, lib))
    dup_path = write(os.path.join(hot, "b", "clip.mov"), b"same")
    dup = ingest_file(session, hot, dup_path)
    process(session, dup, cfg(hot, lib))
    assert dup.status == "duplicate_deleted" and dup.decision == "duplicate_exact"
    assert not os.path.exists(dup_path)
    assert session.query(File).count() == 1


def test_same_content_different_name_goes_to_review(session, dirs):
    hot, lib = dirs
    first = ingest_file(session, hot, write(os.path.join(hot, "x.jpg"), b"content"))
    process(session, first, cfg(hot, lib))
    other = ingest_file(session, hot, write(os.path.join(hot, "y.jpg"), b"content"))
    process(session, other, cfg(hot, lib))
    assert other.status == "duplicate_review"
    assert os.path.isfile(other.current_path) and "/_review/" in other.current_path
    review = session.query(Review).filter_by(kind="exact_name_differs").one()
    assert review.details["incoming_name"] == "y.jpg"


def test_duplicate_is_not_deleted_when_existing_copy_is_missing(session, dirs):
    hot, lib = dirs
    first = ingest_file(session, hot, write(os.path.join(hot, "z.jpg"), b"precious"))
    process(session, first, cfg(hot, lib))
    os.unlink(first.current_path)  # library copy lost / NAS unmounted
    again_path = write(os.path.join(hot, "z.jpg"), b"precious")
    again = ingest_file(session, hot, again_path)
    process(session, again, cfg(hot, lib))
    assert again.status == "accepted" and again.decision == "restore"
    assert os.path.isfile(again.current_path)


def test_trash_mode_moves_instead_of_deleting(session, dirs):
    hot, lib = dirs
    process(session, ingest_file(session, hot, write(os.path.join(hot, "t.txt"), b"t")), cfg(hot, lib))
    dup = ingest_file(session, hot, write(os.path.join(hot, "t.txt"), b"t"))
    process(session, dup, cfg(hot, lib, delete_mode="trash"))
    assert dup.status == "duplicate_deleted" and "/_trash/" in dup.current_path


def test_unmounted_library_defers(session, dirs):
    hot, lib = dirs
    os.unlink(os.path.join(lib, ".dam_library"))
    i = ingest_file(session, hot, write(os.path.join(hot, "w.jpg"), b"w"))
    with pytest.raises(Deferred):
        process(session, i, cfg(hot, lib))
    assert os.path.exists(os.path.join(hot, "w.jpg"))


def test_tax_document_is_protected(session, dirs):
    hot, lib = dirs
    p = write(os.path.join(hot, "scans", "statement.txt"),
              b"Internal Revenue Service Form 1099-INT 2023 interest paid")
    i = ingest_file(session, hot, p)
    process(session, i, cfg(hot, lib))
    asset = session.get(Asset, i.asset_id)
    assert asset.value_tier == "critical" and asset.protected and asset.needs_backup


def test_watcher_waits_for_stable_file_and_ignores_review_folder(session, dirs):
    hot, _ = dirs
    write(os.path.join(hot, "_review", "held.jpg"), b"held")
    write(os.path.join(hot, ".DS_Store"), b"x")
    write(os.path.join(hot, "new.jpg"), b"new")
    seen = {}
    assert scan_once(session, hot, seen, stable_seconds=0.2) == 0  # first sighting
    time.sleep(0.3)
    assert scan_once(session, hot, seen, stable_seconds=0.2) == 1
    assert scan_once(session, hot, seen, stable_seconds=0.2) == 0  # not twice
    assert session.query(Ingest).one().original_filename == "new.jpg"


def test_queue_claim_is_exclusive(session):
    queue.enqueue(session, "ingest")
    session.commit()
    a = queue.claim(session, ["default"], "w1")
    b = queue.claim(session, ["default"], "w2")
    assert a is not None and b is None and a.status == "running"


def test_settings_override_env(session):
    settings_store.update(session, {"delete_mode": "trash"})
    session.commit()
    assert settings_store.get(session, "delete_mode") == "trash"
