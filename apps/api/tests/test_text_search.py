"""Search ignores case, accents and apostrophes, on the server."""

from datetime import date, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import TestingSession, customer_conversation

from app.models import Conversation
from app.routers import conversations as conversations_router
from app.services import ai as ai_service
from app.services.text_search import _ACCENTS, _DROPPED, _SQL_FROM, _SQL_TO, fold, folded_like, sql_fold

from test_api_v1 import _auth, _setup as _v1_setup

SAMPLES = [
    "Gómez", "GÓMEZ", "gomez", "José Gómez", "Ñandú", "ÑANDÚ", "Ünal", "O’Brien", "O'Brien", "O‘Brien", "Oʼ Brien",
    "D´Angelo", "D`Angelo", "Åsa Ørsted", "François Lefèvre", "Çelik Şahin", "Łukasz Żółć", "Ångström",
    "Müller-Weiß", "Zoë", "Ávila Ãngela", "plain ascii 123 +57 300", "100%_done", "", "Nguyễn",
]


def test_fold_strips_case_accents_and_apostrophes():
    assert fold("Gómez") == fold("GOMEZ") == fold("gomez") == "gomez"
    assert fold("ÁÉÍÓÚ áéíóú àèìòù äëïöü âêîôû") == "aeiou aeiou aeiou aeiou aeiou"
    assert fold("Ñandú") == "nandu"
    assert fold("Ünal") == "unal"
    assert fold("O’Brien") == fold("O'Brien") == fold("O‘Brien") == fold("Oʼbrien") == "obrien"
    assert fold("ç Ç ã Ã ø Ø å Å ý ÿ") == "c c a a o o a a y y"


def test_fold_edges():
    assert fold(None) == ""
    assert fold("") == ""
    assert fold("+57 300-111") == "+57 300-111"
    # A letter typed as base + combining accent meets the precomposed one.
    assert fold("Gómez") == "gomez"


def test_table_shapes():
    assert len(_SQL_FROM) == len(_ACCENTS) + len(_DROPPED)
    assert len(_SQL_TO) == len(_ACCENTS)
    assert len(_SQL_TO) < len(_SQL_FROM)
    assert all(letter.isascii() and letter.islower() for letter in _SQL_TO)


def test_sql_fold_matches_fold():
    with TestingSession() as db:
        for sample in SAMPLES:
            assert db.scalar(select(sql_fold(sample))) == fold(sample), sample
        assert db.scalar(select(sql_fold(None))) == ""
        for char, base in _ACCENTS.items():
            assert db.scalar(select(sql_fold(char))) == base, char
        for char in _DROPPED:
            assert db.scalar(select(sql_fold(f"a{char}b"))) == "ab", char


def test_folded_like_escapes_wildcards():
    with TestingSession() as db:
        assert db.scalar(select(folded_like("100% Gómez", "100%"))) is True
        assert db.scalar(select(folded_like("1000 Gómez", "100%"))) is False
        assert db.scalar(select(folded_like("a_b", "a_b"))) is True
        assert db.scalar(select(folded_like("axb", "a_b"))) is False
        assert db.scalar(select(folded_like("a\\b", "a\\b"))) is True


def _portal(client: TestClient):
    customer = client.post("/api/clients", json={"name": "Search Co", "is_active": True}).json()
    client.post(f"/api/clients/{customer['id']}/portal-users", json={"name": "Ana", "email": "ana@search.co", "password": "secure-portal"})
    client.patch(f"/api/clients/{customer['id']}/portal", json={"portal_enabled": True})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "name": "Host", "instructions": "", "personality": "", "model": "", "is_active": True},
    ).json()
    client.post(f"/api/portal/{customer['portal_slug']}/login", json={"email": "ana@search.co", "password": "secure-portal"})
    return customer, agent


GOMEZ_TERMS = ["gomez", "GOMEZ", "Gómez", "jose", "José", "JOSÉ GOMEZ", "  gómez "]
OBRIEN_TERMS = ["obrien", "o'brien", "O’Brien", "OBRIEN"]


def test_portal_contact_search(authenticated_client: TestClient):
    client = authenticated_client
    customer, _ = _portal(client)
    base = f"/api/portal/{customer['portal_slug']}/contacts"
    gomez = client.post(base, json={"name": "José Gómez", "phone": "573001112233", "email": "Jose.G@Correo.co"}).json()
    obrien = client.post(base, json={"name": "Sean O'Brien", "phone": "573004445566"}).json()

    def ids(term):
        return [row["id"] for row in client.get(base, params={"search": term}).json()]

    for term in GOMEZ_TERMS:
        assert ids(term) == [gomez["id"]], term
    for term in OBRIEN_TERMS:
        assert ids(term) == [obrien["id"]], term
    # Phone and email still match, and a non-match still finds nothing.
    assert ids("573001") == [gomez["id"]]
    assert ids("CORREO") == [gomez["id"]]
    assert ids("perez") == []


def test_portal_conversation_search(authenticated_client: TestClient):
    client = authenticated_client
    customer, agent = _portal(client)
    base = f"/api/portal/{customer['portal_slug']}/conversations"
    by_name = customer_conversation(client, agent["id"])["id"]
    by_title = customer_conversation(client, agent["id"])["id"]
    by_obrien = customer_conversation(client, agent["id"])["id"]
    with TestingSession() as db:
        for row in db.scalars(select(Conversation)).all():
            if str(row.id) == by_name:
                row.contact_name = "José Gómez"
                row.title = "Pedido"
            elif str(row.id) == by_title:
                row.title = "Cliente GÓMEZ"
                row.contact_name = "Alguien"
            elif str(row.id) == by_obrien:
                row.contact_name = "Sean O’Brien"
                row.title = "Otro"
        db.commit()

    def ids(term):
        return sorted(row["id"] for row in client.get(base, params={"search": term}).json())

    for term in GOMEZ_TERMS:
        if fold(term).strip() in ("jose", "jose gomez"):
            assert ids(term) == [by_name], term
        else:
            assert ids(term) == sorted([by_name, by_title]), term
    for term in OBRIEN_TERMS:
        assert ids(term) == [by_obrien], term


def test_agency_conversation_search(authenticated_client: TestClient):
    client = authenticated_client
    _, agent = _portal(client)
    first = customer_conversation(client, agent["id"])["id"]
    second = customer_conversation(client, agent["id"])["id"]
    with TestingSession() as db:
        for row in db.scalars(select(Conversation)).all():
            if str(row.id) == first:
                row.contact_name = "José Gómez"
            elif str(row.id) == second:
                row.contact_name = "Sean O'Brien"
        db.commit()

    def ids(term):
        return [row["id"] for row in client.get("/api/conversations/inbox", params={"search": term}).json()]

    for term in GOMEZ_TERMS:
        assert ids(term) == [first], term
    for term in OBRIEN_TERMS:
        assert ids(term) == [second], term


def test_api_v1_contact_search(authenticated_client: TestClient):
    client = authenticated_client
    customer, _, token = _v1_setup(client)
    headers = _auth(token)
    url = f"/api/v1/clients/{customer['id']}/contacts"
    gomez = client.post(url, headers=headers, json={"name": "José Gómez", "phone": "+57 300 111 2233"}).json()
    obrien = client.post(url, headers=headers, json={"name": "Sean O'Brien", "phone": "+57 300 444 5566"}).json()

    def ids(term):
        return [row["id"] for row in client.get(url, headers=headers, params={"search": term}).json()["data"]]

    for term in GOMEZ_TERMS:
        assert ids(term) == [gomez["id"]], term
    for term in OBRIEN_TERMS:
        assert ids(term) == [obrien["id"]], term
    assert ids("573001112233") == [gomez["id"]]
    assert ids("nobody") == []


def test_reply_report_search_matches_the_contact_name(authenticated_client: TestClient, monkeypatch):
    client = authenticated_client
    customer = client.post("/api/clients", json={"name": "Bistro", "is_active": True}).json()
    client.put("/api/providers/openrouter", json={"api_key": "secret"})
    agent = client.post("/api/agents", json={
        "client_id": customer["id"], "provider": "openrouter", "model": "openai/gpt-5.6-luna", "name": "Host",
        "instructions": "", "personality": "", "is_active": True,
    }).json()
    conversation = client.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    with TestingSession() as db:
        db.get(Conversation, conversation["id"]).contact_name = "José Gómez"
        db.commit()
    monkeypatch.setattr(
        conversations_router, "run_completion",
        AsyncMock(return_value=ai_service.Completion(text="Hi", input_tokens=10, output_tokens=1)),
    )
    assert client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "hello"}).status_code == 200

    today = date.today()
    period = f"from={(today - timedelta(days=1)).isoformat()}&to={(today + timedelta(days=1)).isoformat()}"
    for term in ("gomez", "GÓMEZ", "jose", "José"):
        assert client.get(f"/api/reports/replies?{period}&q={term}").json()["total"] == 1, term
    assert client.get(f"/api/reports/replies?{period}&q=nobody").json()["total"] == 0
