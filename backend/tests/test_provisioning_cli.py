"""End-to-end provisioning flows through `app.cli.provision.run` (issue N-09).

Real commits against the test database (see provisioning_helpers.py). The CLI
is driven by a scripted terminal; the URL is only ever read from the terminal's
`reveal` channel, exactly as an operator would see it."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.cli import provision as cli
from app.db.base import SessionLocal, engine
from app.models.certificate import CertificateStatus
from app.models.nfc_tag import NfcTagStatus
from app.services import provisioning as prov
from app.services.certificates import hash_certificate_token
from tests.provisioning_helpers import (
    EOF,
    PRODUCTION_ENV,
    ScriptedTerminal,
    issue_answers,
    lock_answers,
    revoke_answers,
    rotate_answers,
    run_cli,
    world,  # noqa: F401  (fixture)
)


def _issue(world, code=None, uid=None, **kw):  # noqa: F811
    code = code or world.piece()
    uid = uid or world.uid()
    term = ScriptedTerminal(issue_answers(code, uid, **kw))
    return code, uid, term, run_cli(["issue", "--piece", code], term)


def _nothing_changed(world, code):  # noqa: F811
    assert world.certificates(code) == [] and world.tags(code) == []


# --- read-only commands -----------------------------------------------------------------------------


def test_list_and_status_are_read_only_and_need_no_tty(world):  # noqa: F811
    code = world.piece()
    term = ScriptedTerminal(secret_ok=False)

    assert run_cli(["list"], term) == cli.EXIT_OK
    assert code in term.visible_text

    term = ScriptedTerminal(secret_ok=False)
    assert run_cli(["status", "--piece", code], term) == cli.EXIT_OK
    assert "'issue'" in term.visible_text
    assert term.prompts == [] and term.revealed == []
    _nothing_changed(world, code)


def test_unknown_piece_is_exit_3_and_the_code_is_not_echoed(world):  # noqa: F811
    term = ScriptedTerminal(secret_ok=False)
    assert run_cli(["status", "--piece", "NO-SUCH-PIECE-XYZ"], term) == cli.EXIT_PRECONDITION
    assert "NO-SUCH-PIECE-XYZ" not in term.visible_text
    assert "No existe ninguna pieza" in term.visible_text


def test_read_only_sessions_really_refuse_writes(world):  # noqa: F811
    provisioner = cli.Provisioner(ScriptedTerminal(), SessionLocal, "test", "postgresql://u:p@localhost/x_test", "t")
    before = world.piece()

    def attempt_write(db):
        db.execute(text("update piece set name = 'hacked' where public_code = :c"), {"c": before})

    with pytest.raises(cli.CliExit) as info:
        provisioner._read(attempt_write)
    assert info.value.code == cli.EXIT_DATABASE
    with Session(bind=engine) as s:
        assert s.execute(text("select name from piece where public_code = :c"), {"c": before}).scalar_one() != "hacked"


# --- dry-run -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "needs_certificate"),
    [("issue", True), ("rotate", False), ("revoke", False), ("lock", False)],
)
def test_dry_run_of_an_ineligible_piece_is_exit_3(world, command, needs_certificate):  # noqa: F811
    code = world.piece()
    if needs_certificate:
        world.issued(code, program=True)  # `issue` is refused once a certificate exists
    before = (len(world.certificates(code)), len(world.tags(code)))
    term = ScriptedTerminal(secret_ok=False)

    assert run_cli([command, "--piece", code, "--dry-run"], term) == cli.EXIT_PRECONDITION

    assert term.prompts == [] and term.revealed == []
    assert (len(world.certificates(code)), len(world.tags(code))) == before


def test_issue_dry_run_asks_nothing_generates_no_token_and_changes_nothing(world):  # noqa: F811
    code = world.piece()
    term = ScriptedTerminal(secret_ok=False)  # dry-run works without a TTY: it has no secret

    assert run_cli(["issue", "--piece", code, "--dry-run"], term) == cli.EXIT_OK

    assert term.prompts == [] and term.revealed == []
    assert "SIMULACRO" in term.visible_text
    assert "<43 caracteres" in term.visible_text
    assert term.tokens() == []
    _nothing_changed(world, code)


def test_dry_run_never_calls_the_token_generator(world, monkeypatch):  # noqa: F811
    code = world.piece()
    token, uid, tag_id, cert_id = world.issued(code, program=True)  # set up with the real generator first

    def forbidden():
        raise AssertionError("dry-run must never generate a token")

    monkeypatch.setattr("app.services.certificates.generate_certificate_token", forbidden)
    fresh = world.piece()
    for command, target in (("issue", fresh), ("rotate", code), ("revoke", code), ("lock", code)):
        term = ScriptedTerminal(secret_ok=False)
        assert run_cli([command, "--piece", target, "--dry-run"], term) == cli.EXIT_OK, command
        assert term.prompts == [] and term.revealed == []

    [cert] = world.certificates(code)
    assert cert.status == CertificateStatus.active and world.tags(code)[0].status == NfcTagStatus.programmed
    assert world.certificates(fresh) == [] and world.tags(fresh) == []


# --- issue ---------------------------------------------------------------------------------------------------------------


def test_issue_happy_path(world):  # noqa: F811
    code, uid, term, exit_code = _issue(world)

    assert exit_code == cli.EXIT_OK
    assert term.unused_answers() == 0
    [token] = term.tokens()
    [cert] = world.certificates(code)
    [tag] = world.tags(code)
    assert cert.status == CertificateStatus.active
    assert cert.token_hash == hash_certificate_token(token)  # only the hash is stored
    assert tag.physical_uid == uid and tag.status == NfcTagStatus.programmed and tag.programmed_at is not None
    assert tag.piece_id == cert.piece_id
    assert (tag.chip_model, tag.frequency, tag.protocol) == ("NTAG213", "13.56 MHz", "ISO 14443A")
    assert world.resolves(token, code)
    assert [line.split()[1] for line in tag.notes.splitlines()] == ["issue", "program"]
    assert term.urls() == [f"http://127.0.0.1:5500/c/{token}"]  # test env = rehearsal base
    assert "ENSAYO LOCAL" in term.visible_text


def test_issue_normalizes_the_uid_the_operator_typed(world):  # noqa: F811
    code = world.piece()
    uid = world.uid()
    typed = uid.replace(":", "").lower()
    term = ScriptedTerminal(issue_answers(code, typed, tail=uid[-5:]))
    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_OK
    assert world.tags(code)[0].physical_uid == uid
    assert f"Interpretado como {uid}" in term.visible_text


def test_issue_in_production_asks_for_the_environment_first_and_uses_the_real_host(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal(issue_answers(code, uid, production=True))

    assert run_cli(["issue", "--piece", code], term, environment=PRODUCTION_ENV) == cli.EXIT_OK

    assert term.prompts[0].startswith("Está operando sobre PRODUCCIÓN")
    [url] = term.urls()
    assert url.startswith("https://artesanfc.com/c/") and "api." not in url
    assert "ENSAYO LOCAL" not in term.visible_text


def test_a_wrong_environment_confirmation_stops_everything(world):  # noqa: F811
    code = world.piece()
    term = ScriptedTerminal(["prod"])
    assert run_cli(["issue", "--piece", code], term, environment=PRODUCTION_ENV) == cli.EXIT_ABORTED
    assert len(term.prompts) == 1
    _nothing_changed(world, code)


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param({"published": False}, id="piece-not-published"),
        pytest.param({"artisan_published": False}, id="artisan-not-published"),
    ],
)
def test_issue_refuses_an_unpublished_piece_or_artisan(world, setup):  # noqa: F811
    code = world.piece(**setup)
    term = ScriptedTerminal([])
    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert "NO" in term.visible_text
    assert term.prompts == []  # refused before asking anything else
    _nothing_changed(world, code)


def test_issue_refuses_a_piece_that_already_has_a_certificate(world):  # noqa: F811
    code = world.piece()
    world.issued(code, program=True)
    term = ScriptedTerminal([])
    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert "ya tiene un certificado ACTIVO" in term.visible_text
    assert len(world.certificates(code)) == 1


def test_issue_refuses_a_uid_that_is_already_registered(world):  # noqa: F811
    other = world.piece()
    _, taken_uid, _, _ = world.issued(other, program=True)
    code = world.piece()
    term = ScriptedTerminal([code, taken_uid, "s"])

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert "ya está registrado" in term.visible_text
    _nothing_changed(world, code)


def test_issue_gives_up_after_three_invalid_uids(world):  # noqa: F811
    code = world.piece()
    term = ScriptedTerminal([code, "nope", "05:00:00:00:00:00:00", ""])
    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert term.warned.count(cli._UID_ERROR_MESSAGES["not_hex"]) == 1
    _nothing_changed(world, code)


def test_issue_gives_up_when_the_uid_is_never_confirmed(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal([code, uid, "n", uid, "", uid, "n"])
    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PRECONDITION
    _nothing_changed(world, code)


@pytest.mark.parametrize("wrong", ["", "n", "emitir", "EMITIR", "EMITIR otra"])
def test_issue_needs_the_exact_typed_confirmation(world, wrong):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)
    answers[3] = wrong
    term = ScriptedTerminal(answers)

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_ABORTED
    assert term.revealed == []
    _nothing_changed(world, code)


def test_issue_needs_the_piece_code_retyped(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)
    answers[0] = code.lower()
    term = ScriptedTerminal(answers)
    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_ABORTED
    _nothing_changed(world, code)


@pytest.mark.parametrize("position", range(0, 4))
def test_closing_the_input_before_the_commit_changes_nothing(world, position):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)[:position] + [EOF]
    term = ScriptedTerminal(answers)

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_ABORTED
    assert term.revealed == []
    _nothing_changed(world, code)


def test_a_competing_writer_between_preflight_and_commit_is_caught_by_the_recheck(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)
    confirmation = answers[3]

    def competing_issue(prompt):
        world.issued(code)  # another operator issues this piece meanwhile
        return confirmation

    answers[3] = competing_issue
    term = ScriptedTerminal(answers)

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert term.revealed == []
    assert len(world.certificates(code)) == 1  # only the competitor's
    assert world.tags_with_uid(uid) == []  # nothing of ours was left behind


def test_issue_is_atomic_when_the_certificate_step_fails(world, monkeypatch):  # noqa: F811
    code, uid = world.piece(), world.uid()

    def boom(db, piece_id):
        raise RuntimeError("certificate step failed")

    monkeypatch.setattr(prov, "issue_certificate", boom)
    term = ScriptedTerminal(issue_answers(code, uid))

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_UNEXPECTED
    assert term.revealed == []
    _nothing_changed(world, code)  # the tag registered + assigned before it was rolled back
    assert world.tags_with_uid(uid) == []


class _FailingCommit(Session):
    """A session whose N-th commit fails like a dropped connection."""

    fail_on: int = 1
    commits: int = 0

    def commit(self):
        type(self).commits += 1
        if type(self).commits == type(self).fail_on:
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        super().commit()


def _failing_factory(fail_on: int):
    class Sub(_FailingCommit):
        pass

    Sub.fail_on, Sub.commits = fail_on, 0
    return lambda: Sub(bind=engine, autoflush=False)


def test_a_failed_commit_shows_no_url_and_leaves_nothing(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal(issue_answers(code, uid))

    exit_code = run_cli(["issue", "--piece", code], term, session_factory=_failing_factory(1))

    assert exit_code == cli.EXIT_DATABASE
    assert term.revealed == []  # the URL is never shown before the hash is committed
    assert "no se confirmó" in term.visible_text
    _nothing_changed(world, code)


def test_a_failed_program_commit_is_a_partial_state_with_the_exact_next_command(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal(issue_answers(code, uid, scan="s"))

    exit_code = run_cli(["issue", "--piece", code], term, session_factory=_failing_factory(2))

    assert exit_code == cli.EXIT_PARTIAL
    assert f"rotate --piece {code}" in term.visible_text
    [cert] = world.certificates(code)
    assert cert.status == CertificateStatus.active  # issued, and the token was shown once
    assert world.tags(code)[0].status == NfcTagStatus.available  # never marked programmed


def test_show_again_repeats_the_same_url_without_issuing_another_certificate(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)
    answers[5:6] = ["m", "m", "e"]  # show again twice, then "written"
    term = ScriptedTerminal(answers)

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_OK
    assert len(term.revealed) == 3 and len(set(term.urls())) == 1
    assert len(world.certificates(code)) == 1


def test_a_readback_that_does_not_match_keeps_the_tag_unprogrammed_until_corrected(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)
    # "written", read-back does NOT match, show again, "written", read-back matches
    answers[5:7] = ["e", "n", "m", "e", "s"]
    term = ScriptedTerminal(answers)

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_OK
    assert world.tags(code)[0].status == NfcTagStatus.programmed
    assert "escritura no está confirmada" in term.visible_text


def test_a_uid_that_does_not_match_is_never_marked_programmed(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    other_tail = "00:00" if uid[-5:] != "00:00" else "11:11"
    answers = issue_answers(code, uid)
    answers[7:9] = [other_tail, other_tail, other_tail]
    term = ScriptedTerminal(answers)

    exit_code = run_cli(["issue", "--piece", code], term)

    assert exit_code == cli.EXIT_PARTIAL
    assert "OTRO tag físico" in term.visible_text
    assert world.tags(code)[0].status == NfcTagStatus.available
    assert f"rotate --piece {code}" in term.visible_text


def test_a_failed_phone_scan_is_a_partial_state_and_says_to_rotate(world):  # noqa: F811
    code, uid, term, exit_code = _issue(world, scan="n")

    assert exit_code == cli.EXIT_PARTIAL
    assert f"rotate --piece {code}" in term.visible_text
    assert world.tags(code)[0].status == NfcTagStatus.programmed  # recorded before the scan, by design
    assert world.certificates(code)[0].status == CertificateStatus.active


@pytest.mark.parametrize("position", [4, 5])
def test_losing_the_terminal_after_the_commit_is_exit_6_and_rotate_recovers(world, position):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)[:position] + [EOF]
    term = ScriptedTerminal(answers)

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PARTIAL
    assert f"rotate --piece {code}" in term.visible_text
    assert world.tags(code)[0].status == NfcTagStatus.available

    # Recovery (decision H): the token is not recoverable, so rotate.
    uid_tail = world.tags(code)[0].physical_uid[-5:]
    rotate = ScriptedTerminal(rotate_answers(code, "lost", tail=uid_tail, choice="m"))
    assert run_cli(["rotate", "--piece", code], rotate) == cli.EXIT_OK
    [token] = rotate.tokens()
    assert world.resolves(token, code)
    assert world.tags(code)[0].status == NfcTagStatus.programmed


def test_aborting_after_the_commit_can_revoke_the_undeployed_certificate(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    answers = issue_answers(code, uid)[:5] + ["a", ""]  # abort, accept the default (revoke)
    term = ScriptedTerminal(answers)

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_ABORTED
    [cert] = world.certificates(code)
    assert cert.status == CertificateStatus.revoked and cert.revocation_reason == "not-deployed"
    assert world.tags(code) == [] or world.tags(code)[0].status == NfcTagStatus.retired
    assert world.state(code).active_certificate is None
    # ... and the piece can be issued again with a new tag
    _, _, term2, code2 = _issue(world, code=code)
    assert code2 == cli.EXIT_OK


def test_aborting_and_declining_the_revoke_leaves_a_partial_state(world):  # noqa: F811
    code, uid = world.piece(), world.uid()
    term = ScriptedTerminal(issue_answers(code, uid)[:5] + ["a", "n"])

    assert run_cli(["issue", "--piece", code], term) == cli.EXIT_PARTIAL
    assert world.certificates(code)[0].status == CertificateStatus.active


# --- rotate --------------------------------------------------------------------------------------------------------------


def test_rotate_rewriting_the_same_tag(world):  # noqa: F811
    code = world.piece()
    old_token, uid, tag_id, old_cert = world.issued(code, program=True)
    term = ScriptedTerminal(rotate_answers(code, "compromised", tail=uid[-5:], choice="m"))

    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_OK

    [new_token] = term.tokens()
    assert new_token != old_token
    assert world.resolves(new_token, code) and not world.resolves(old_token, code)
    old, new = world.certificates(code)
    assert old.status == CertificateStatus.revoked and old.revocation_reason == "compromised"
    assert new.status == CertificateStatus.active
    [tag] = world.tags(code)
    assert tag.id == tag_id and tag.status == NfcTagStatus.programmed
    assert "rewrite" in tag.notes


def test_rotate_onto_a_new_tag_retires_the_old_one(world):  # noqa: F811
    code = world.piece()
    old_token, old_uid, old_tag, _ = world.issued(code, program=True)
    new_uid = world.uid()
    term = ScriptedTerminal(rotate_answers(code, "damaged", tail=new_uid[-5:], choice="n", new_uid=new_uid))

    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_OK

    tags = {t.physical_uid: t for t in world.tags(code)}
    assert tags[old_uid].status == NfcTagStatus.retired
    assert tags[new_uid].status == NfcTagStatus.programmed
    assert sum(t.status == NfcTagStatus.programmed for t in tags.values()) == 1
    assert not world.resolves(old_token, code) and world.resolves(term.tokens()[0], code)


def test_rotate_of_a_locked_tag_only_offers_a_new_tag(world):  # noqa: F811
    code = world.piece()
    old_token, old_uid, _, _ = world.issued(code, lock=True)
    new_uid = world.uid()
    term = ScriptedTerminal(rotate_answers(code, "lost", tail=new_uid[-5:], new_uid=new_uid))

    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_OK

    assert "BLOQUEADO" in term.visible_text
    statuses = {t.physical_uid: t.status for t in world.tags(code)}
    assert statuses == {old_uid: NfcTagStatus.retired, new_uid: NfcTagStatus.programmed}
    # The locked tag's `locked_at` history is preserved.
    assert [t.locked_at for t in world.tags(code) if t.physical_uid == old_uid][0] is not None


def test_rotate_a_new_tag_with_a_taken_uid_keeps_the_old_certificate(world):  # noqa: F811
    other = world.piece()
    _, taken, _, _ = world.issued(other, program=True)
    code = world.piece()
    old_token, uid, _, _ = world.issued(code, program=True)
    term = ScriptedTerminal([code, "lost", "n", taken, "s"])

    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert world.resolves(old_token, code)  # nothing was revoked


@pytest.mark.parametrize("answers_tail", [["ROTAR", "rotar x"], [""]])
def test_rotate_needs_the_exact_typed_confirmation_and_changes_nothing(world, answers_tail):  # noqa: F811
    code = world.piece()
    old_token, uid, _, _ = world.issued(code, program=True)
    term = ScriptedTerminal([code, "lost", "m", answers_tail[0]])

    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_ABORTED
    assert world.resolves(old_token, code)
    assert term.revealed == []


def test_rotate_rejects_an_unknown_reason_three_times(world):  # noqa: F811
    code = world.piece()
    old_token, *_ = world.issued(code, program=True)
    term = ScriptedTerminal([code, "porque si", "otro", "quiero"])
    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert world.resolves(old_token, code)


def test_rotate_without_an_active_certificate_is_refused(world):  # noqa: F811
    code = world.piece()
    term = ScriptedTerminal([])
    assert run_cli(["rotate", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert "no tiene un certificado activo" in term.visible_text


def test_rotate_after_a_failed_scan_recovers_the_piece(world):  # noqa: F811
    code, uid, term, exit_code = _issue(world, scan="n")
    assert exit_code == cli.EXIT_PARTIAL
    bad_token = term.tokens()[0]

    rotate = ScriptedTerminal(rotate_answers(code, "wrong-tag", tail=uid[-5:], choice="m"))
    assert run_cli(["rotate", "--piece", code], rotate) == cli.EXIT_OK
    assert not world.resolves(bad_token, code) and world.resolves(rotate.tokens()[0], code)


# --- revoke ------------------------------------------------------------------------------------------------------------------


def test_revoke_revokes_retires_and_shows_no_secret(world):  # noqa: F811
    code = world.piece()
    token, uid, _, _ = world.issued(code, program=True)
    term = ScriptedTerminal(revoke_answers(code, "lost"), secret_ok=False)  # no TTY needed: no secret

    assert run_cli(["revoke", "--piece", code], term) == cli.EXIT_OK

    assert term.revealed == []
    [cert] = world.certificates(code)
    assert cert.status == CertificateStatus.revoked and cert.revoked_at is not None and cert.revocation_reason == "lost"
    assert [t.status for t in world.tags(code)] == [NfcTagStatus.retired]
    assert not world.resolves(token, code)
    assert "IRREVERSIBLE" in term.visible_text


def test_revoke_leaves_the_piece_issueable_again(world):  # noqa: F811
    code = world.piece()
    world.issued(code, program=True)
    assert run_cli(["revoke", "--piece", code], ScriptedTerminal(revoke_answers(code, "replaced"))) == cli.EXIT_OK

    _, _, term, exit_code = _issue(world, code=code)

    assert exit_code == cli.EXIT_OK
    assert len(world.certificates(code)) == 2


@pytest.mark.parametrize("typed", ["", "REVOCAR", "revocar", "REVOCAR x", "s"])
def test_revoke_needs_the_exact_typed_confirmation(world, typed):  # noqa: F811
    code = world.piece()
    token, *_ = world.issued(code, program=True)
    term = ScriptedTerminal([code, "lost", typed])

    assert run_cli(["revoke", "--piece", code], term) == cli.EXIT_ABORTED
    assert world.resolves(token, code)
    assert world.certificates(code)[0].status == CertificateStatus.active


def test_revoke_without_an_active_certificate_is_refused(world):  # noqa: F811
    code = world.piece()
    term = ScriptedTerminal([], secret_ok=False)
    assert run_cli(["revoke", "--piece", code], term) == cli.EXIT_PRECONDITION


def test_revoke_works_even_when_the_piece_was_unpublished_afterwards(world):  # noqa: F811
    code = world.piece()
    token, *_ = world.issued(code, program=True)
    with Session(bind=engine) as s:
        s.execute(text("update piece set publication_status = 'draft' where public_code = :c"), {"c": code})
        s.commit()

    assert run_cli(["revoke", "--piece", code], ScriptedTerminal(revoke_answers(code, "other"))) == cli.EXIT_OK
    assert world.certificates(code)[0].status == CertificateStatus.revoked


# --- lock ---------------------------------------------------------------------------------------------------------------------------


def test_lock_records_the_physical_lock_after_three_confirmations(world):  # noqa: F811
    code = world.piece()
    _, uid, _, _ = world.issued(code, program=True)
    term = ScriptedTerminal(lock_answers(code, uid), secret_ok=False)

    assert run_cli(["lock", "--piece", code], term) == cli.EXIT_OK

    [tag] = world.tags(code)
    assert tag.status == NfcTagStatus.locked and tag.locked_at is not None
    assert "NO bloquea el tag" in term.visible_text  # the CLI never claims to lock anything itself
    assert "lock" in tag.notes


@pytest.mark.parametrize("failing", [1, 2, 3])
def test_lock_is_cancelled_when_any_physical_confirmation_is_missing(world, failing):  # noqa: F811
    code = world.piece()
    _, uid, _, _ = world.issued(code, program=True)
    answers = lock_answers(code, uid)
    answers[failing] = "n"
    term = ScriptedTerminal(answers[: failing + 1])

    assert run_cli(["lock", "--piece", code], term) == cli.EXIT_ABORTED
    assert world.tags(code)[0].status == NfcTagStatus.programmed


def test_lock_needs_the_uid_tail_and_the_typed_confirmation(world):  # noqa: F811
    code = world.piece()
    _, uid, _, _ = world.issued(code, program=True)

    wrong_tail = ScriptedTerminal([code, "s", "s", "s", "00:00", "00:00", "00:00"])
    assert run_cli(["lock", "--piece", code], wrong_tail) == cli.EXIT_PRECONDITION

    wrong_word = ScriptedTerminal([code, "s", "s", "s", uid[-5:], "BLOQUEAR"])
    assert run_cli(["lock", "--piece", code], wrong_word) == cli.EXIT_ABORTED
    assert world.tags(code)[0].status == NfcTagStatus.programmed


def test_lock_is_refused_unless_the_tag_is_programmed(world):  # noqa: F811
    code = world.piece()
    world.issued(code)  # available, never programmed
    term = ScriptedTerminal([], secret_ok=False)
    assert run_cli(["lock", "--piece", code], term) == cli.EXIT_PRECONDITION
    assert "programado" in term.visible_text

    code2 = world.piece()
    world.issued(code2, lock=True)
    term2 = ScriptedTerminal([], secret_ok=False)
    assert run_cli(["lock", "--piece", code2], term2) == cli.EXIT_PRECONDITION
    assert "ya está registrado como bloqueado" in term2.visible_text


def test_issue_never_locks_and_offers_lock_only_as_a_separate_step(world):  # noqa: F811
    code, uid, term, exit_code = _issue(world)
    assert exit_code == cli.EXIT_OK
    assert world.tags(code)[0].status == NfcTagStatus.programmed
    assert world.tags(code)[0].locked_at is None
    assert "aparte ('lock')" in term.visible_text
