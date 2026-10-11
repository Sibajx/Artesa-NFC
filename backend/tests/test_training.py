"""Capacitaciones: training manuals with the owner's review (Training permission)."""
from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models.admin_account import AdminAccount
from app.models.audit_event import AuditEvent
from app.services import mailer
from tests.test_accounts_and_authorization import NEW, OWNER, H, owner_client  # noqa: F401
from tests.test_visits import API, files, get, jpeg, media_root, post  # noqa: F401

WRITTEN = "Se amasa el barro, se deja reposar un día y se modela a mano antes del horno."


def manual_body(**extra):
    return {"title": "Barro negro paso a paso", "craft": "Alfarería", "written": WRITTEN, **extra}


def new_manual(client, **extra):
    r = post(client, "/training", manual_body(**extra))
    assert r.status_code == 201, r.text
    return r.json()


def give_training(client, db_session, email=NEW):
    post(client, "/accounts", {"email": email, "role": "editor"})
    db_session.execute(update(AdminAccount).where(AdminAccount.email == email).values(permissions=["view", "training"]))
    db_session.commit()


def add_image(client, manual, caption=None, email=OWNER):
    q = f"?caption={caption}" if caption else ""
    return client.post(f"{API}/training/{manual['id']}/images{q}", content=jpeg(size=(80, 60)),
                       headers=H(email, **{"Content-Type": "image/jpeg"}))


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.org")
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def test_only_people_with_the_training_permission_can_read_or_write(owner_client, db_session):
    new_manual(owner_client)
    post(owner_client, "/accounts", {"email": NEW, "role": "editor"})  # an editor has no Training
    assert get(owner_client, "/training", NEW).status_code == 403
    assert post(owner_client, "/training", manual_body(), NEW).status_code == 403
    give_training(owner_client, db_session)
    assert get(owner_client, "/training", NEW).status_code == 200
    assert post(owner_client, "/training", manual_body(title="Telar de cintura", craft="Textil"), NEW).status_code == 201


def test_create_edit_and_list(owner_client):
    m = new_manual(owner_client)
    assert m["status"] == "draft" and m["created_by"] == OWNER and m["images"] == []
    r = owner_client.patch(f"{API}/training/{m['id']}", json={"visual": "Dibujo 1: el barro.", "next_steps": "Pedir fotos."}, headers=H())
    assert r.status_code == 200 and r.json()["visual"] == "Dibujo 1: el barro." and r.json()["written"] == WRITTEN
    new_manual(owner_client, title="Bordado de Huipil", craft="Textil")
    assert [x["craft"] for x in get(owner_client, "/training").json()["data"]] == ["Alfarería", "Textil"]
    assert [x["title"] for x in get(owner_client, "/training?craft=Textil").json()["data"]] == ["Bordado de Huipil"]
    assert post(owner_client, "/training", {"title": "ab", "craft": "Textil"}).json()["error"]["code"] == "title_required"
    assert post(owner_client, "/training", {"title": "Algo bueno", "craft": " "}).json()["error"]["code"] == "craft_required"


def test_the_review_flow_and_who_may_answer(owner_client, db_session, outbox):
    give_training(owner_client, db_session)
    m = post(owner_client, "/training", manual_body(), NEW).json()
    # A manual without a written version cannot ask for review.
    blank = post(owner_client, "/training", manual_body(title="Vacío por ahora", written=None), NEW).json()
    assert post(owner_client, f"/training/{blank['id']}/submit", None, NEW).json()["error"]["code"] == "written_required"
    # Nothing to answer until it is in review, and only the owner answers.
    assert post(owner_client, f"/training/{m['id']}/review", {"decision": "approve"}).json()["error"]["code"] == "invalid_status"
    sub = post(owner_client, f"/training/{m['id']}/submit", None, NEW)
    assert sub.status_code == 200 and sub.json()["status"] == "in_review" and sub.json()["submitted_at"]
    assert [s for s in outbox if s[1].endswith("espera tu revisión")] and NEW in outbox[0][2]
    assert post(owner_client, f"/training/{m['id']}/submit", None, NEW).json()["error"]["code"] == "invalid_status"
    assert post(owner_client, f"/training/{m['id']}/review", {"decision": "approve"}, NEW).status_code == 403
    # Asking for changes must say what; the author is told.
    assert post(owner_client, f"/training/{m['id']}/review", {"decision": "changes"}).json()["error"]["code"] == "note_required"
    r = post(owner_client, f"/training/{m['id']}/review", {"decision": "changes", "note": "Falta el tiempo de horneado."})
    assert r.json()["status"] == "changes_requested" and r.json()["review_note"] == "Falta el tiempo de horneado."
    assert r.json()["reviewed_by"] == OWNER
    mails = [s for s in outbox if s[0] == NEW]
    assert mails and mails[-1][1].endswith("necesita cambios") and "Falta el tiempo" in mails[-1][2]
    # Fix it, ask again, approve.
    owner_client.patch(f"{API}/training/{m['id']}", json={"written": WRITTEN + " Hornear 6 horas."}, headers=H(NEW))
    assert get(owner_client, f"/training/{m['id']}").json()["status"] == "changes_requested"
    post(owner_client, f"/training/{m['id']}/submit", None, NEW)
    ok = post(owner_client, f"/training/{m['id']}/review", {"decision": "approve"})
    assert ok.json()["status"] == "approved"
    assert any(s[0] == NEW and s[1].endswith("fue aprobado") for s in outbox)
    actions = set(db_session.execute(select(AuditEvent.action).where(AuditEvent.entity_type == "training_manual")).scalars())
    assert {"training.created", "training.submitted", "training.changes_requested", "training.approved"} <= actions


def test_editing_an_approved_or_pending_manual_sends_it_back_to_draft(owner_client):
    m = new_manual(owner_client)
    post(owner_client, f"/training/{m['id']}/submit")
    r = owner_client.patch(f"{API}/training/{m['id']}", json={"title": "Barro negro, versión corta"}, headers=H())
    assert r.json()["status"] == "draft" and r.json()["submitted_at"] is None
    post(owner_client, f"/training/{m['id']}/submit")
    post(owner_client, f"/training/{m['id']}/review", {"decision": "approve"})
    assert get(owner_client, f"/training/{m['id']}").json()["status"] == "approved"
    owner_client.patch(f"{API}/training/{m['id']}", json={"next_steps": "Agregar fotos."}, headers=H())
    assert get(owner_client, f"/training/{m['id']}").json()["status"] == "draft"


def test_drawings_are_private_limited_and_removed_with_the_manual(owner_client, media_root):
    m = new_manual(owner_client)
    r = add_image(owner_client, m, caption="El barro listo")
    assert r.status_code == 201, r.text
    image = r.json()["images"][0]
    assert image["caption"] == "El barro listo" and image["url"].startswith("/api/admin/v1/training/images/")
    stored = files(media_root)
    assert len(stored) == 1 and stored[0].relative_to(media_root).parts[:2] == ("privado", "capacitaciones")
    got = owner_client.get(image["url"].replace("/api/admin/v1", API), headers=H())
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and "no-store" in got.headers["cache-control"]
    assert owner_client.get(image["url"]).status_code == 401
    assert owner_client.post(f"{API}/training/{m['id']}/images", content=b"nope",
                             headers=H(**{"Content-Type": "image/jpeg"})).status_code == 422
    for _ in range(11):
        assert add_image(owner_client, m).status_code == 201
    assert add_image(owner_client, m).json()["error"]["code"] == "too_many_images"
    # Removing one drawing deletes its file; deleting the manual deletes the rest (owner only).
    assert post(owner_client, f"/training/images/{image['id']}/delete").status_code == 200
    assert len(files(media_root)) == 11
    assert post(owner_client, f"/training/{m['id']}/delete").status_code == 204
    assert files(media_root) == [] and get(owner_client, f"/training/{m['id']}").status_code == 404


def test_deleting_is_for_the_owner_and_changing_a_drawing_reopens_the_review(owner_client, db_session, media_root):
    give_training(owner_client, db_session)
    m = post(owner_client, "/training", manual_body(), NEW).json()
    assert post(owner_client, f"/training/{m['id']}/delete", None, NEW).status_code == 403
    post(owner_client, f"/training/{m['id']}/submit", None, NEW)
    post(owner_client, f"/training/{m['id']}/review", {"decision": "approve"})
    assert add_image(owner_client, m, email=NEW).status_code == 201
    assert get(owner_client, f"/training/{m['id']}").json()["status"] == "draft"
