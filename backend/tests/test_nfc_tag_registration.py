"""`normalize_physical_uid` and `register_nfc_tag` (issue N-09)."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.models.nfc_tag import NfcTag, NfcTagStatus
from app.services import nfc_tags
from app.services.nfc_tags import (
    InvalidPhysicalUid,
    NfcTagUidAlreadyRegistered,
    normalize_physical_uid,
    register_nfc_tag,
    retire_nfc_tag,
)

CANONICAL = "04:A1:B2:C3:D4:E5:F6"


@pytest.mark.parametrize(
    "raw",
    [
        "04:A1:B2:C3:D4:E5:F6",
        "04a1b2c3d4e5f6",
        "04A1B2C3D4E5F6",
        "04-a1-b2-c3-d4-e5-f6",
        "04 a1 b2 c3 d4 e5 f6",
        "  04:a1:B2:c3:D4:e5:F6\n",
        "04a1:b2c3-d4e5 f6",
    ],
)
def test_uid_is_normalized_to_upper_case_colon_separated_bytes(raw):
    assert normalize_physical_uid(raw) == CANONICAL


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("", "empty"),
        ("  :- ", "empty"),
        ("04:A1:B2:C3:D4:E5:GG", "not_hex"),
        ("0x04A1B2C3D4E5F6", "not_hex"),
        ("04:A1:B2:C3:D4:E5", "wrong_length"),  # 6 bytes
        ("04:A1:B2:C3:D4:E5:F6:07", "wrong_length"),  # 8 bytes
        ("04A1B2C3D4E5F", "wrong_length"),  # odd number of digits
        ("05:A1:B2:C3:D4:E5:F6", "not_nxp"),
        ("A" * 65, "too_long"),
    ],
)
def test_invalid_uids_are_rejected_with_a_reason(raw, reason):
    with pytest.raises(InvalidPhysicalUid) as info:
        normalize_physical_uid(raw)
    assert info.value.reason == reason


def test_a_pasted_certificate_url_or_token_is_rejected_and_never_echoed():
    url = "https://artesanfc.com/c/" + "A" * 43
    for pasted in (url, "A" * 43, "abc_-" * 9):
        with pytest.raises(InvalidPhysicalUid) as info:
            normalize_physical_uid(pasted)
        assert pasted not in str(info.value) and pasted not in repr(info.value)


def test_non_string_input_is_rejected():
    with pytest.raises(InvalidPhysicalUid):
        normalize_physical_uid(None)  # type: ignore[arg-type]


def test_register_creates_an_available_unassigned_ntag213(db_session):
    tag = register_nfc_tag(db_session, physical_uid="04a1b2c3d4e5f6")

    assert tag.physical_uid == CANONICAL
    assert tag.status == NfcTagStatus.available
    assert tag.piece_id is None
    assert tag.programmed_at is None and tag.locked_at is None
    assert (tag.chip_model, tag.frequency, tag.protocol) == ("NTAG213", "13.56 MHz", "ISO 14443A")


def test_register_rejects_a_duplicate_uid_in_any_spelling(db_session):
    register_nfc_tag(db_session, physical_uid=CANONICAL)

    with pytest.raises(NfcTagUidAlreadyRegistered) as info:
        register_nfc_tag(db_session, physical_uid="04-a1-b2-c3-d4-e5-f6")

    assert CANONICAL not in str(info.value)


def test_a_retired_tags_uid_stays_taken(db_session):
    tag = register_nfc_tag(db_session, physical_uid=CANONICAL)
    retire_nfc_tag(db_session, tag)

    with pytest.raises(NfcTagUidAlreadyRegistered):
        register_nfc_tag(db_session, physical_uid=CANONICAL)


def test_register_rejects_an_invalid_uid_without_touching_the_database(db_session):
    with pytest.raises(InvalidPhysicalUid):
        register_nfc_tag(db_session, physical_uid="nope")
    assert db_session.execute(text("select count(*) from nfc_tag where physical_uid is null")).scalar_one() == 0


def test_register_stores_the_notes_it_is_given(db_session):
    tag = register_nfc_tag(db_session, physical_uid=CANONICAL, notes="lote 1")
    assert tag.notes == "lote 1"


def test_the_mapped_unique_constraint_name_matches_the_migration(db_session):
    # register_nfc_tag maps this constraint name to NfcTagUidAlreadyRegistered
    # for a concurrent duplicate; it must be the name PostgreSQL really gave
    # the unnamed UNIQUE(physical_uid) of migration 9b8bb430770d.
    names = db_session.execute(
        text("select conname from pg_constraint where conrelid = 'nfc_tag'::regclass and contype = 'u'")
    ).scalars().all()
    assert nfc_tags._UQ_PHYSICAL_UID in names


def test_register_persists_through_the_orm_like_any_tag(db_session):
    register_nfc_tag(db_session, physical_uid=CANONICAL)
    row = db_session.query(NfcTag).filter_by(physical_uid=CANONICAL).one()
    assert row.status == NfcTagStatus.available
