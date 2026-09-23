"""current/previous switching is one atomic rename (N-08, ADR-027)."""
import os
import threading

import pytest

import deploy_layout as dl
import release_common as rc

A = "20260921T030000Z-aaaaaaaaaaaa"
B = "20260921T030100Z-bbbbbbbbbbbb"


@pytest.fixture()
def layout(tmp_path):
    for rid in (A, B):
        (tmp_path / "releases" / rid).mkdir(parents=True)
    return dl.Layout(tmp_path)


def test_link_is_relative_and_replaced_in_place(layout):
    dl.atomic_symlink(layout, layout.current, A)
    assert os.readlink(layout.current) == f"releases/{A}"
    dl.atomic_symlink(layout, layout.current, B)
    assert dl.read_link(layout, layout.current) == B
    assert [p.name for p in layout.root.iterdir() if ".tmp" in p.name] == []


def test_a_continuous_reader_never_observes_the_link_missing(layout):
    dl.atomic_symlink(layout, layout.current, A)
    stop, problems, reads = threading.Event(), [], [0]

    def reader():
        while not stop.is_set():
            try:
                target = os.readlink(layout.current)
                assert target in (f"releases/{A}", f"releases/{B}")
                assert os.path.isdir(layout.current)     # resolves through the link
            except (FileNotFoundError, AssertionError) as exc:
                problems.append(repr(exc))
                return
            reads[0] += 1

    threads = [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for i in range(400):
        dl.atomic_symlink(layout, layout.current, B if i % 2 == 0 else A)
    stop.set()
    for t in threads:
        t.join()
    assert problems == [] and reads[0] > 200


def test_naive_unlink_then_symlink_would_be_observable(layout):
    """Negative control: the gap-prone way DOES lose the link, proving the
    reader test above can detect the failure it guards against."""
    os.symlink(f"releases/{A}", layout.current)
    stop, missing = threading.Event(), [0]

    def reader():
        while not stop.is_set():
            try:
                os.readlink(layout.current)
            except FileNotFoundError:
                missing[0] += 1

    t = threading.Thread(target=reader); t.start()
    for _ in range(1500):
        os.unlink(layout.current)
        os.symlink(f"releases/{B}", layout.current)
        os.unlink(layout.current)
        os.symlink(f"releases/{A}", layout.current)
    stop.set(); t.join()
    assert missing[0] > 0


def test_a_failed_replace_leaves_the_old_link_and_no_temp_file(layout, monkeypatch):
    dl.atomic_symlink(layout, layout.current, A)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk on fire")))
    with pytest.raises(OSError):
        dl.atomic_symlink(layout, layout.current, B)
    monkeypatch.undo()
    assert dl.read_link(layout, layout.current) == A
    assert [p.name for p in layout.root.iterdir() if ".tmp" in p.name] == []


def test_a_stale_temp_link_from_a_crashed_run_is_replaced(layout):
    os.symlink("junk", layout.root / f"current.tmp-{os.getpid()}")
    dl.atomic_symlink(layout, layout.current, A)
    assert dl.read_link(layout, layout.current) == A


@pytest.mark.parametrize("bad", ["../etc", "x", "20260921T030000Z-AAAAAAAAAAAA", "20261301T030000Z-aaaaaaaaaaaa", "a/b"])
def test_only_valid_release_ids_can_be_linked(layout, bad):
    with pytest.raises(rc.OpsError):
        dl.atomic_symlink(layout, layout.current, bad)


def test_links_that_do_not_point_into_releases_are_refused(layout):
    os.symlink("/etc", layout.current)
    with pytest.raises(rc.OpsError, match="does not point into releases"):
        dl.read_link(layout, layout.current)
    os.unlink(layout.current)
    os.symlink(f"releases/{A}/../{B}", layout.current)   # normalizes to a valid release: accepted
    assert dl.read_link(layout, layout.current) == B
    os.unlink(layout.current)
    os.symlink("releases/20260921T030000Z-cccccccccccc", layout.current)
    with pytest.raises(rc.OpsError, match="does not exist"):
        dl.read_link(layout, layout.current)


def test_a_real_directory_named_current_is_the_hybrid_layout_and_is_refused(layout):
    (layout.root / "current").mkdir()
    with pytest.raises(rc.OpsError, match="not a symlink"):
        dl.read_link(layout, layout.current)


def test_previous_is_updated_before_current_so_a_crash_between_leaves_a_consistent_pair(layout):
    dl.atomic_symlink(layout, layout.current, A)
    dl.atomic_symlink(layout, layout.previous, A)
    dl.atomic_symlink(layout, layout.previous, A)   # step 1 of a switch to B
    assert dl.read_link(layout, layout.current) == A and dl.read_link(layout, layout.previous) == A
