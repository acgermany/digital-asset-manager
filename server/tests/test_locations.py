import os

from dam.core import locations


def test_host_container_round_trip(host_root):
    sub = os.path.join(host_root, "Pictures")
    os.makedirs(sub, exist_ok=True)
    assert locations.to_host(sub) == "/Users/tester/Pictures"
    assert locations.to_container("/Users/tester/Pictures") == sub
    assert locations.to_container("/etc") is None


def test_browse_refuses_outside_roots():
    try:
        locations.browse("/etc")
    except PermissionError:
        return
    raise AssertionError("expected PermissionError")


def test_browse_lists_dirs(host_root):
    os.makedirs(os.path.join(host_root, "B"), exist_ok=True)
    os.makedirs(os.path.join(host_root, ".hidden"), exist_ok=True)
    out = locations.browse(host_root)
    names = [d["name"] for d in out["dirs"]]
    assert "B" in names and ".hidden" not in names
    assert out["host_path"] == "/Users/tester"
