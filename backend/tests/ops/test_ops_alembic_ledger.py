"""Alembic graph parsing, the migration ledger and deployment classification
(N-08, ADR-027). Forward-only: nothing here downgrades."""
import json
from pathlib import Path

import pytest

import deploy_db as ddb
import release_common as rc
from tests.ops.helpers import LEDGER, build, make_repo, migration

BACKEND = Path(__file__).resolve().parents[2]


def revs(*pairs):
    return rc.parse_alembic_revisions({f"alembic/versions/{r}_x.py": migration(r, d).encode() for r, d in pairs})


CHAIN = revs(("a1", None), ("b2", "a1"), ("c3", "b2"))
LEDGER3 = {"a1": {"class": "baseline", "note": ""}, "b2": {"class": "additive", "note": ""}, "c3": {"class": "additive", "note": ""}}


def test_real_repo_has_one_head_and_a_complete_ledger():
    files = {f"alembic/versions/{p.name}": p.read_bytes() for p in (BACKEND / "alembic" / "versions").glob("*.py")}
    revisions = rc.parse_alembic_revisions(files)
    assert rc.alembic_heads(revisions) == ["895974720462"]
    ledger = rc.parse_ledger((BACKEND / "ops" / "migration-classes.json").read_bytes())
    rc.check_ledger_complete(revisions, ledger)
    assert {e["class"] for e in ledger.values()} <= set(rc.MIGRATION_CLASSES)


def test_topological_order_and_heads():
    assert [r["revision"] for r in CHAIN] == ["a1", "b2", "c3"]
    assert rc.alembic_heads(CHAIN) == ["c3"]


def test_two_heads_are_reported():
    assert rc.alembic_heads(revs(("a1", None), ("b2", "a1"), ("c3", "a1"))) == ["b2", "c3"]


def test_merge_revisions_with_tuple_down_revision():
    files = {"alembic/versions/m_x.py": b"revision = 'm'\ndown_revision = ('b2', 'c2')\n",
             "alembic/versions/a_x.py": migration("a1", None).encode(), "alembic/versions/b_x.py": migration("b2", "a1").encode(),
             "alembic/versions/c_x.py": migration("c2", "a1").encode()}
    parsed = rc.parse_alembic_revisions(files)
    assert rc.alembic_heads(parsed) == ["m"] and parsed[-1]["down_revision"] == ["b2", "c2"]


@pytest.mark.parametrize("files,match", [
    ({"alembic/versions/x.py": b"revision = 'a1'\ndown_revision = 'zz'\n"}, "unknown parent"),
    ({"alembic/versions/x.py": b"revision = 'a1'\ndown_revision = None\n", "alembic/versions/y.py": b"revision = 'a1'\ndown_revision = None\n"}, "duplicate"),
    ({"alembic/versions/x.py": b"revision = 'a1'\ndown_revision = 'b2'\n", "alembic/versions/y.py": b"revision = 'b2'\ndown_revision = 'a1'\n"}, "cycle"),
    ({"alembic/versions/x.py": b"def broken(:\n"}, "cannot parse"),
    ({"alembic/versions/x.py": b"revision = compute()\ndown_revision = None\n"}, "non-literal"),
    ({"alembic/versions/x.py": b"x = 1\n"}, "without revision"),
])
def test_bad_migration_files_are_refused(files, match):
    with pytest.raises(rc.OpsError, match=match):
        rc.parse_alembic_revisions(files)


def test_pending_revisions_from_each_starting_point():
    assert rc.pending_revisions(CHAIN, None, "c3") == ["a1", "b2", "c3"]
    assert rc.pending_revisions(CHAIN, "a1", "c3") == ["b2", "c3"]
    assert rc.pending_revisions(CHAIN, "c3", "c3") == []


def test_unknown_or_ahead_database_revision_is_rejected():
    with pytest.raises(KeyError):
        rc.pending_revisions(CHAIN, "zzz", "c3")
    with pytest.raises(KeyError):  # DB ahead of an older release: release head b2, DB at c3
        rc.pending_revisions(CHAIN, "c3", "b2")


def test_classification():
    assert rc.classify_pending([], LEDGER3) == rc.CLASS_CODE_ONLY
    assert rc.classify_pending(["b2", "c3"], LEDGER3) == rc.CLASS_ADDITIVE
    assert rc.classify_pending(["a1", "b2"], LEDGER3) == rc.CLASS_ADDITIVE  # baseline on an empty database
    breaking = {**LEDGER3, "c3": {"class": "breaking", "note": ""}}
    assert rc.classify_pending(["b2", "c3"], breaking) == rc.CLASS_BREAKING


def test_ledger_must_classify_every_revision_and_know_no_others():
    with pytest.raises(rc.OpsError, match="without a class.*c3"):
        rc.check_ledger_complete(CHAIN, {k: v for k, v in LEDGER3.items() if k != "c3"})
    with pytest.raises(rc.OpsError, match="unknown revision.*zz"):
        rc.check_ledger_complete(CHAIN, {**LEDGER3, "zz": {"class": "additive", "note": ""}})


@pytest.mark.parametrize("payload", [b"not json", b'{"schema_version": 2, "revisions": {}}', b'{"schema_version": 1}',
                                     b'{"schema_version": 1, "revisions": {"a": {"class": "safe", "note": ""}}}',
                                     b'{"schema_version": 1, "revisions": {"a": {"class": "additive"}}}',
                                     b'{"schema_version": 1, "revisions": {"a": {"class": "additive", "note": "", "extra": 1}}}'])
def test_malformed_ledgers_are_refused(payload):
    with pytest.raises(rc.OpsError):
        rc.parse_ledger(payload)


def test_a_new_revision_without_a_ledger_class_fails_the_build(tmp_path):
    repo = make_repo(tmp_path, {"backend/alembic/versions/ccc333_new.py": migration("ccc333", "bbb222")})
    with pytest.raises(rc.OpsError, match="without a class.*ccc333"):
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)


def test_a_stale_ledger_entry_fails_the_build(tmp_path):
    ledger = json.loads(json.dumps(LEDGER)); ledger["revisions"]["ghost"] = {"class": "additive", "note": ""}
    repo = make_repo(tmp_path, {"backend/ops/migration-classes.json": json.dumps(ledger)})
    with pytest.raises(rc.OpsError, match="unknown revision"):
        build(repo, tmp_path / "o", ref="HEAD", rehearsal=True)


# --- rollback compatibility (never downgrades; reads the ledger) ---------------------------------

def test_rollback_to_the_head_the_database_is_at_is_compatible():
    assert rc.rollback_compatibility(CHAIN, LEDGER3, "c3", "c3") == (True, "compatible")


def test_rollback_over_additive_migrations_is_compatible():
    ok, why = rc.rollback_compatibility(CHAIN, LEDGER3, "c3", "a1")
    assert ok and "2 additive" in why


def test_rollback_over_a_breaking_migration_is_incompatible():
    breaking = {**LEDGER3, "b2": {"class": "breaking", "note": ""}}
    ok, why = rc.rollback_compatibility(CHAIN, breaking, "c3", "a1")
    assert not ok and "b2" in why


def test_rollback_when_database_is_behind_or_unknown_is_incompatible():
    assert not rc.rollback_compatibility(CHAIN, LEDGER3, "a1", "c3")[0]
    assert not rc.rollback_compatibility(CHAIN, LEDGER3, "zzz", "a1")[0]
    assert not rc.rollback_compatibility(CHAIN, LEDGER3, None, "a1")[0]
    assert not rc.rollback_compatibility(CHAIN, LEDGER3, "c3", "not-in-graph")[0]


# --- planning through deploy_db -----------------------------------------------------------------------

def release_dict(revisions):
    return {"alembic": {"head": rc.alembic_heads(revisions)[0], "revisions": revisions}}


def test_plan_code_only_additive_and_breaking():
    state = lambda rev: ddb.DbState(180006, [rev] if rev else [], {})
    assert ddb.plan_migration(release_dict(CHAIN), LEDGER3, state("c3")).deployment_class == rc.CLASS_CODE_ONLY
    plan = ddb.plan_migration(release_dict(CHAIN), LEDGER3, state("a1"))
    assert plan.deployment_class == rc.CLASS_ADDITIVE and plan.pending == ["b2", "c3"]
    breaking = {**LEDGER3, "c3": {"class": "breaking", "note": ""}}
    assert ddb.plan_migration(release_dict(CHAIN), breaking, state("a1")).deployment_class == rc.CLASS_BREAKING


def test_plan_distinguishes_ahead_from_unknown():
    older = release_dict(CHAIN[:2])
    state = ddb.DbState(180006, ["c3"], {})
    with pytest.raises(rc.OpsError, match="AHEAD") as err:
        ddb.plan_migration(older, LEDGER3, state, other_known={"a1", "b2", "c3"})
    assert err.value.code == rc.Exit.ALEMBIC
    with pytest.raises(rc.OpsError, match="unknown to this release") as err:
        ddb.plan_migration(older, LEDGER3, ddb.DbState(180006, ["mystery"], {}), other_known={"a1", "b2"})
    assert err.value.code == rc.Exit.ALEMBIC


def test_multiple_revisions_in_the_database_are_unsupported():
    with pytest.raises(rc.OpsError, match="more than one"):
        ddb.plan_migration(release_dict(CHAIN), LEDGER3, ddb.DbState(180006, ["b2", "c3"], {}))
