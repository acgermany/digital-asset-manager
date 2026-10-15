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
    queue.enqueue(session, "ingest", queue="ingest")
    session.commit()
    a = queue.claim(session, ["ingest"], "w1")
    b = queue.claim(session, ["ingest"], "w2")
    assert a is not None and b is None and a.status == "running"


def test_settings_override_env(session):
    settings_store.update(session, {"delete_mode": "trash"})
    session.commit()
    assert settings_store.get(session, "delete_mode") == "trash"


def test_watcher_ignores_empty_placeholders(session, dirs):
    hot, _ = dirs
    write(os.path.join(hot, "big.mov"), b"")       # what Finder creates before copying data
    seen = {}
    for _ in range(3):
        scan_once(session, hot, seen, stable_seconds=0)
    assert session.query(Ingest).count() == 0


def test_empty_file_is_never_a_duplicate(session, dirs):
    hot, lib = dirs
    i = ingest_file(session, hot, write(os.path.join(hot, "a.mov"), b""))
    from dam.core.ingest import IngestError
    with pytest.raises(IngestError, match="Empty file"):
        process(session, i, cfg(hot, lib))
    assert session.query(Asset).count() == 0


def test_dropped_folder_keeps_its_structure_so_same_names_dont_collide(session, dirs):
    hot, lib = dirs
    a = ingest_file(session, hot, write(os.path.join(hot, "Grandma", "Trip 2019", "IMG_0001.jpg"), b"trip"))
    b = ingest_file(session, hot, write(os.path.join(hot, "Grandma", "Xmas", "IMG_0001.jpg"), b"xmas"))
    for i in (a, b):
        process(session, i, cfg(hot, lib))
        assert i.status == "accepted"
    assert a.current_path == os.path.join(lib, "Grandma", "Trip 2019", "IMG_0001.jpg")
    assert b.current_path == os.path.join(lib, "Grandma", "Xmas", "IMG_0001.jpg")
    assert session.query(Review).filter_by(kind="name_collision").count() == 0


def test_loose_file_goes_to_dated_unspecified_folder(session, dirs):
    hot, lib = dirs
    i = ingest_file(session, hot, write(os.path.join(hot, "loose.txt"), b"loose"))
    process(session, i, cfg(hot, lib))
    rel = os.path.relpath(i.current_path, lib).split(os.sep)
    assert rel[0] == "unspecified" and len(rel) == 4 and rel[-1] == "loose.txt"


def test_same_path_different_checksum_is_held_for_review(session, dirs):
    hot, lib = dirs
    first = ingest_file(session, hot, write(os.path.join(hot, "Mom", "notes.txt"), b"version one"))
    process(session, first, cfg(hot, lib))
    second_path = write(os.path.join(hot, "Mom", "notes.txt"), b"version two")
    second = ingest_file(session, hot, second_path)
    process(session, second, cfg(hot, lib))

    assert second.status == "collision_review" and second.decision == "name_collision"
    assert not os.path.exists(second_path)
    assert "/_review/" in second.current_path and os.path.isfile(second.current_path)
    with open(first.current_path, "rb") as f:
        assert f.read() == b"version one"  # existing library file untouched
    review = session.query(Review).filter_by(kind="name_collision").one()
    assert review.details["library_path"] == first.current_path
    assert review.details["incoming_sha256"] != review.details["existing_sha256"]


def test_collision_keep_both_renames_incoming_next_to_existing(session, dirs):
    from dam.core.reviews import resolve
    hot, lib = dirs
    process(session, ingest_file(session, hot, write(os.path.join(hot, "Mom", "a.txt"), b"1")), cfg(hot, lib))
    second = ingest_file(session, hot, write(os.path.join(hot, "Mom", "a.txt"), b"2"))
    process(session, second, cfg(hot, lib))
    review = session.query(Review).filter_by(kind="name_collision").one()
    resolve(session, review, "keep_both", cfg(hot, lib))
    assert second.status == "accepted"
    assert second.current_path == os.path.join(lib, "Mom", "a (1).txt")
    assert session.get(Asset, second.asset_id).files[0].path == second.current_path


def test_collision_delete_incoming_removes_file_and_orphan_asset(session, dirs):
    from dam.core.reviews import resolve
    hot, lib = dirs
    process(session, ingest_file(session, hot, write(os.path.join(hot, "Mom", "b.txt"), b"1")), cfg(hot, lib))
    second = ingest_file(session, hot, write(os.path.join(hot, "Mom", "b.txt"), b"2"))
    process(session, second, cfg(hot, lib))
    held = second.current_path
    review = session.query(Review).filter_by(kind="name_collision").one()
    resolve(session, review, "delete_incoming", cfg(hot, lib))
    assert second.status == "discarded" and not os.path.exists(held)
    assert session.query(Asset).count() == 1 and review.status == "resolved"


def test_identical_untracked_file_at_library_path_is_adopted(session, dirs):
    hot, lib = dirs
    write(os.path.join(lib, "Dad", "c.txt"), b"same")  # copied into the library by hand
    p = write(os.path.join(hot, "Dad", "c.txt"), b"same")
    i = ingest_file(session, hot, p)
    process(session, i, cfg(hot, lib))
    assert i.status == "accepted" and i.current_path == os.path.join(lib, "Dad", "c.txt")
    assert not os.path.exists(p) and session.query(Review).filter_by(kind="name_collision").count() == 0


def test_watcher_inventories_every_file_in_a_dropped_folder(session, dirs):
    hot, _ = dirs
    for rel in ("Drop/a.jpg", "Drop/sub/a.jpg", "Drop/sub/deeper/b.mov"):
        write(os.path.join(hot, rel), rel.encode())
    seen = {}
    scan_once(session, hot, seen, stable_seconds=0)
    assert scan_once(session, hot, seen, stable_seconds=0) == 3
    assert {os.path.relpath(i.original_path, hot) for i in session.query(Ingest)} == {
        "Drop/a.jpg", "Drop/sub/a.jpg", "Drop/sub/deeper/b.mov"}
