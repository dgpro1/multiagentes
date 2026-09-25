"""Merging two leads into one, Kommo style.

A lead is a conversation. Merging folds the secondary into the primary without
deleting it: it becomes a linked thread that keeps its channel, chat key and
messages but is read through the primary. These tests hold every merge rule, the
validation, the number alias, how lists and the detail present a merged lead,
replies through a chosen thread, the guards on acting through a linked thread,
the candidate search and the doors' permissions. How each channel routes an
inbound message to a linked thread lives in ``test_lead_merge_inbound.py``.
"""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import (
    Agency,
    Client,
    Contact,
    ContactIdentity,
    ContactTag,
    Conversation,
    Message,
    MessageAttachment,
    PipelineStage,
    Team,
    now_utc,
)
from app.services.conversation_state import resolve_idle_ai_conversations
from conftest import TestingSession
from test_lead_card import Lead

ZERO = "00000000-0000-0000-0000-000000000000"
BASE_TIME = now_utc() - timedelta(hours=2)


@pytest.fixture
def lead(authenticated_client: TestClient) -> Lead:
    return Lead(authenticated_client)


# --- helpers ------------------------------------------------------------------------------------


def _ids(lead: Lead) -> tuple[uuid.UUID, uuid.UUID]:
    with TestingSession() as db:
        row = db.get(Conversation, lead.cid)
        return row.agency_id, row.agent_id


def conv(lead: Lead, channel: str = "widget", chat: str | None = None, *, name: str | None = None, **fields) -> str:
    """A customer conversation of the lead's client, straight in the database."""
    agency_id, agent_id = _ids(lead)
    with TestingSession() as db:
        row = Conversation(
            agency_id=agency_id, client_id=uuid.UUID(lead.id), agent_id=agent_id, channel=channel,
            external_chat_id=chat, contact_name=name, title=name or "Chat", **fields,
        )
        db.add(row)
        db.commit()
        return str(row.id)


def say(cid: str, text: str, who: str = "visitor", minute: int = 0, **extra) -> str:
    """A message on a conversation, ``minute`` minutes after a fixed start so the order is explicit."""
    with TestingSession() as db:
        message = Message(
            conversation_id=uuid.UUID(cid), role="user" if who == "visitor" else "assistant", sender_type=who,
            sender_name="Visitor" if who == "visitor" else "Staff", content=text,
            created_at=BASE_TIME + timedelta(minutes=minute), **extra,
        )
        db.add(message)
        db.commit()
        return str(message.id)


def contact(lead: Lead, name: str = "", *, phone: str | None = None, email: str | None = None, company: str | None = None) -> str:
    with TestingSession() as db:
        row = Contact(client_id=uuid.UUID(lead.id), name=name, phone=phone, email=email, company=company)
        db.add(row)
        db.commit()
        return str(row.id)


def state(cid: str) -> Conversation:
    with TestingSession() as db:
        row = db.get(Conversation, uuid.UUID(cid))
        db.expunge(row)
        return row


def portal_merge(lead: Lead, primary: str, secondary: str, session: TestClient | None = None):
    return (session or lead.admin).post(
        f"{lead.base}/leads/merge", json={"primary_conversation_id": primary, "secondary_conversation_id": secondary}
    )


def agency_merge(lead: Lead, primary: str, secondary: str):
    return lead.agency.post(
        f"/api/clients/{lead.id}/leads/merge", json={"primary_conversation_id": primary, "secondary_conversation_id": secondary}
    )


def portal_items(lead: Lead, **params) -> list[dict]:
    response = lead.admin.get(f"{lead.base}/conversations", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def activity(cid: str) -> list[dict]:
    with TestingSession() as db:
        rows = db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(cid), Message.kind == "activity")).all()
        return [dict(row.activity) for row in rows]


def merged_pair(lead: Lead, **primary_fields) -> tuple[str, str]:
    primary = conv(lead, "widget", "widget:p", name="Primary", **primary_fields)
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="Secondary")
    assert portal_merge(lead, primary, secondary).status_code == 200
    return primary, secondary


# --- the nine steps -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "primary_price,secondary_price,expected",
    [(None, 500, 500.0), (0, 500, 500.0), (300, 500, 300.0), (300, None, 300.0), (None, None, None), (None, 0, None)],
)
def test_the_primary_keeps_its_budget_unless_it_has_none(lead: Lead, primary_price, secondary_price, expected):
    primary = conv(lead, name="P", deal_value=primary_price)
    secondary = conv(lead, "whatsapp", "573000000001@s.whatsapp.net", name="S", deal_value=secondary_price)
    merged = portal_merge(lead, primary, secondary)
    assert merged.status_code == 200, merged.text
    card = merged.json()["primary"]
    assert card["conversation_id"] == primary and card["deal_value"] == expected
    assert (float(state(primary).deal_value) if state(primary).deal_value is not None else None) == expected
    assert state(secondary).deal_value is None
    details = activity(primary)[-1]
    # The audit keeps each lead's price as it was before the rule ran.
    assert details["primary"]["price"] == (float(primary_price) if primary_price is not None else None)
    assert details["secondary"]["price"] == (float(secondary_price) if secondary_price is not None else None)


def test_custom_values_only_fill_what_the_primary_lacks(lead: Lead):
    primary = conv(lead, name="P", custom_values={"plan": "gold", "empty": None})
    secondary = conv(lead, "whatsapp", "573000000001@s.whatsapp.net", name="S",
                     custom_values={"plan": "silver", "source": "ads", "empty": "later"})
    assert portal_merge(lead, primary, secondary).status_code == 200
    # The primary's own keys are never overwritten, even an empty one; the rest is added.
    assert state(primary).custom_values == {"plan": "gold", "empty": None, "source": "ads"}
    assert state(secondary).custom_values == {}
    assert activity(primary)[-1]["secondary"]["custom_fields"] == {"plan": "silver", "source": "ads", "empty": "later"}


def test_responsible_assignee_and_team_are_adopted_only_when_the_primary_has_none(lead: Lead):
    ana, bob = lead.members["ana"], lead.member("bob") and lead.members["bob"]
    with TestingSession() as db:
        team = Team(client_id=uuid.UUID(lead.id), name="Sales")
        db.add(team)
        db.commit()
        team_id = team.id
    bare = conv(lead, name="Bare", mode="human")
    full = conv(lead, "whatsapp", "573000000001@s.whatsapp.net", name="Full", mode="human",
                responsible_id=uuid.UUID(bob), assignee_id=uuid.UUID(bob), team_id=team_id)
    assert portal_merge(lead, bare, full).status_code == 200
    adopted = state(bare)
    assert str(adopted.responsible_id) == bob and str(adopted.assignee_id) == bob and adopted.team_id == team_id
    # The secondary no longer carries a responsible of its own.
    assert state(full).responsible_id is None
    keeps = conv(lead, name="Keeps", responsible_id=uuid.UUID(ana), assignee_id=uuid.UUID(ana), mode="human")
    other = conv(lead, "whatsapp", "573000000002@s.whatsapp.net", name="Other", mode="human", responsible_id=uuid.UUID(bob))
    assert portal_merge(lead, keeps, other).status_code == 200
    kept = state(keeps)
    assert str(kept.responsible_id) == ana and str(kept.assignee_id) == ana


def test_two_different_contacts_are_merged_into_the_primary_ones(lead: Lead):
    with TestingSession() as db:
        tag_a = ContactTag(client_id=uuid.UUID(lead.id), name="VIP")
        tag_b = ContactTag(client_id=uuid.UUID(lead.id), name="Wholesale")
        db.add_all([tag_a, tag_b])
        db.commit()
        tags = {"vip": tag_a.id, "wholesale": tag_b.id}
    keep = contact(lead, "Maria", email="maria@example.com")
    gone = contact(lead, "Maria G.", phone="573001112233", email="other@example.com", company="Acme")
    with TestingSession() as db:
        keep_row, gone_row = db.get(Contact, uuid.UUID(keep)), db.get(Contact, uuid.UUID(gone))
        vip, wholesale = db.get(ContactTag, tags["vip"]), db.get(ContactTag, tags["wholesale"])
        keep_row.tags.append(vip)
        gone_row.tags.extend([vip, wholesale])
        db.add(ContactIdentity(client_id=uuid.UUID(lead.id), contact_id=uuid.UUID(gone), provider="instagram",
                               external_account_id="acct", external_user_id="ig-1"))
        db.commit()
    primary = conv(lead, name="P", contact_id=uuid.UUID(keep))
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="S", contact_id=uuid.UUID(gone))
    merged = portal_merge(lead, primary, secondary)
    assert merged.status_code == 200, merged.text
    with TestingSession() as db:
        assert db.get(Contact, uuid.UUID(gone)) is None
        survivor = db.get(Contact, uuid.UUID(keep))
        # The primary's own data wins; what it lacked (phone, company) comes from the other one.
        assert survivor.email == "maria@example.com" and survivor.phone == "573001112233" and survivor.company == "Acme"
        assert sorted(tag.name for tag in survivor.tags) == ["VIP", "Wholesale"]
        identities = db.scalars(select(ContactIdentity).where(ContactIdentity.contact_id == survivor.id)).all()
        assert {(row.provider, row.external_user_id) for row in identities} >= {("instagram", "ig-1"), ("phone", "573001112233")}
        assert {str(row.contact_id) for row in db.scalars(select(Conversation).where(Conversation.id.in_([uuid.UUID(primary), uuid.UUID(secondary)])))} == {keep}
    assert merged.json()["primary"]["contact"]["id"] == keep
    assert merged.json()["primary"]["contact"]["company"] == "Acme"


def test_a_lead_without_a_contact_adopts_the_other_ones(lead: Lead):
    owner = contact(lead, "Lucia", phone="573005550000")
    bare = conv(lead, name="Bare")
    known = conv(lead, "whatsapp", "573005550000@s.whatsapp.net", name="Known", contact_id=uuid.UUID(owner))
    assert portal_merge(lead, bare, known).status_code == 200
    assert str(state(bare).contact_id) == owner and str(state(known).contact_id) == owner
    # The other way round the secondary is pointed at the primary's contact.
    known2 = conv(lead, name="Known2", contact_id=uuid.UUID(contact(lead, "Pablo")))
    bare2 = conv(lead, "whatsapp", "573006660000@s.whatsapp.net", name="Bare2")
    assert portal_merge(lead, known2, bare2).status_code == 200
    assert state(bare2).contact_id == state(known2).contact_id


def test_the_same_contact_on_both_leads_is_left_alone(lead: Lead):
    person = contact(lead, "Ana", phone="573007770000")
    first = conv(lead, name="One", contact_id=uuid.UUID(person))
    second = conv(lead, "whatsapp", "573007770000@s.whatsapp.net", name="Two", contact_id=uuid.UUID(person))
    assert portal_merge(lead, first, second).status_code == 200
    with TestingSession() as db:
        assert db.get(Contact, uuid.UUID(person)) is not None
    assert str(state(second).contact_id) == person


def test_the_secondary_becomes_a_linked_thread_that_keeps_its_channel_and_messages(lead: Lead):
    stage = lead.admin.post(f"{lead.base}/pipeline/stages", json={"name": "Won", "color": "#22c55e"}).json()
    primary = conv(lead, "widget", "widget:p", name="P", mode="human")
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="S", mode="ai", pipeline_stage_id=uuid.UUID(stage["id"]))
    kept = say(secondary, "hola from whatsapp", minute=3)
    assert portal_merge(lead, primary, secondary).status_code == 200
    row = state(secondary)
    assert str(row.primary_conversation_id) == primary
    assert row.channel == "whatsapp" and row.external_chat_id == "573001112233@s.whatsapp.net"
    # Its lead data lives on the primary now; its messages never move.
    assert row.pipeline_stage_id is None and row.deal_value is None and row.custom_values == {}
    with TestingSession() as db:
        assert str(db.get(Message, uuid.UUID(kept)).conversation_id) == secondary
    # The lead behaves as one: the primary's mode is copied onto the absorbed thread.
    assert row.mode == "human"


def test_the_audit_line_keeps_what_each_lead_had(lead: Lead):
    primary = conv(lead, "widget", "widget:p", name="Maria", deal_value=100)
    secondary = conv(lead, "instagram", "ig-99", name="maria_ig", deal_value=250, custom_values={"plan": "gold"})
    merged = portal_merge(lead, primary, secondary)
    assert merged.status_code == 200
    with TestingSession() as db:
        line = db.scalar(select(Message).where(Message.conversation_id == uuid.UUID(primary), Message.kind == "activity"))
        assert line.content == f"Ana merged lead #{state(secondary).number} into lead #{state(primary).number}"
        details = dict(line.activity)
    assert details.pop("event") == "entity_merged"
    assert set(details) == {"primary_number", "secondary_number", "primary", "secondary"}
    assert details["primary_number"] == state(primary).number and details["secondary_number"] == state(secondary).number
    assert set(details["primary"]) == {"number", "name", "price", "currency", "created_at", "channels"}
    assert set(details["secondary"]) == {"number", "name", "price", "currency", "created_at", "channels", "custom_fields"}
    assert details["primary"]["name"] == "Maria" and details["primary"]["price"] == 100.0
    assert details["primary"]["channels"] == ["widget"] and details["secondary"]["channels"] == ["instagram"]
    assert details["secondary"]["price"] == 250.0 and details["secondary"]["custom_fields"] == {"plan": "gold"}
    assert details["primary"]["currency"] == details["secondary"]["currency"] == "USD"
    assert details["primary"]["created_at"].startswith(str(state(primary).created_at.year))


def test_swapping_primary_and_secondary_swaps_who_survives_and_whose_number_is_the_alias(lead: Lead):
    first = conv(lead, "widget", "widget:1", name="First")
    second = conv(lead, "whatsapp", "573000000009@s.whatsapp.net", name="Second")
    merged = portal_merge(lead, second, first)
    assert merged.status_code == 200 and merged.json()["primary"]["conversation_id"] == second
    assert merged.json()["secondary_number"] == state(first).number
    assert str(state(first).primary_conversation_id) == second and state(second).primary_conversation_id is None
    # The alias resolves to the survivor, whichever it is.
    resolved = lead.admin.get(f"{lead.base}/conversations/number/{state(first).number}")
    assert resolved.status_code == 200 and resolved.json()["id"] == second


# --- validation ----------------------------------------------------------------------------------


def test_validation_of_the_pair(lead: Lead):
    one = conv(lead, name="One")
    two = conv(lead, "whatsapp", "573000000003@s.whatsapp.net", name="Two")
    assert portal_merge(lead, one, one).status_code == 409
    assert portal_merge(lead, one, ZERO).status_code == 404
    assert portal_merge(lead, ZERO, one).status_code == 404
    assert portal_merge(lead, one, two).status_code == 200
    # Already the same lead, whichever way the pair is named (the linked one resolves to its primary).
    for pair in ((one, two), (two, one)):
        again = portal_merge(lead, *pair)
        assert again.status_code == 409, again.text
        assert "same lead" in again.json()["detail"]
    assert agency_merge(lead, one, two).status_code == 409
    # Nothing changed by the refusals.
    assert str(state(two).primary_conversation_id) == one


def test_a_lead_of_another_client_or_agency_is_never_reachable(lead: Lead, authenticated_client: TestClient):
    mine = conv(lead, name="Mine")
    other = Lead(authenticated_client, name="Other Co")
    theirs = conv(other, name="Theirs")
    # Portal door: the other client's lead does not exist here, on either side of the pair.
    assert portal_merge(lead, mine, theirs).status_code == 404
    assert portal_merge(lead, theirs, mine).status_code == 404
    # Agency door: the same, even inside one agency, because the route names the client.
    assert agency_merge(lead, mine, theirs).status_code == 404
    # A client of another agency is a stranger for the agency's own session.
    with TestingSession() as db:
        agency = Agency(name="Rival", slug="rival")
        db.add(agency)
        db.flush()
        rival = Client(agency_id=agency.id, name="Rival Co", portal_slug="rival-co")
        db.add(rival)
        db.commit()
        rival_id = str(rival.id)
    assert lead.agency.post(f"/api/clients/{rival_id}/leads/merge",
                            json={"primary_conversation_id": mine, "secondary_conversation_id": theirs}).status_code == 404
    assert state(mine).primary_conversation_id is None and state(theirs).primary_conversation_id is None


def test_a_playground_rehearsal_is_not_a_lead(lead: Lead):
    rehearsal = conv(lead, "playground", None, name="Rehearsal")
    real = conv(lead, name="Real")
    assert portal_merge(lead, real, rehearsal).status_code == 404


def test_merging_a_lead_that_already_has_threads_flattens_the_group(lead: Lead):
    root = conv(lead, "widget", "widget:root", name="Root")
    middle = conv(lead, "whatsapp", "573000000010@s.whatsapp.net", name="Middle")
    leaf = conv(lead, "instagram", "ig-leaf", name="Leaf")
    assert portal_merge(lead, middle, leaf).status_code == 200
    # The secondary owns a linked thread: it moves to the primary, no chains.
    assert portal_merge(lead, root, middle).status_code == 200
    assert str(state(middle).primary_conversation_id) == root and str(state(leaf).primary_conversation_id) == root
    assert state(root).primary_conversation_id is None
    # A linked thread named as the primary stands for its lead.
    extra = conv(lead, "messenger", "fb-1", name="Extra")
    merged = portal_merge(lead, leaf, extra)
    assert merged.status_code == 200 and merged.json()["primary"]["conversation_id"] == root
    assert str(state(extra).primary_conversation_id) == root
    assert [thread["channel"] for thread in lead.admin.get(f"{lead.base}/conversations/{root}").json()["linked_threads"]] == [
        "widget", "whatsapp", "instagram", "messenger",
    ]


# --- the number is an alias ----------------------------------------------------------------------


def test_the_absorbed_number_resolves_to_the_primary_on_every_door(lead: Lead):
    primary, secondary = merged_pair(lead)
    alias = state(secondary).number
    portal = lead.admin.get(f"{lead.base}/conversations/number/{alias}")
    assert portal.status_code == 200 and portal.json()["id"] == primary
    agency = lead.agency.get(f"/api/clients/{lead.id}/conversations/number/{alias}")
    assert agency.status_code == 200 and agency.json()["id"] == primary
    # The public API reads the linked thread's id as its lead, and lists only leads.
    token = _v1_token(lead)
    assert token.get(f"/api/v1/clients/{lead.id}/conversations/{secondary}").json()["id"] == primary
    listed = token.get(f"/api/v1/clients/{lead.id}/conversations").json()["data"]
    assert primary in [item["id"] for item in listed] and secondary not in [item["id"] for item in listed]
    messages = token.get(f"/api/v1/clients/{lead.id}/conversations/{secondary}/messages").json()
    assert messages["total"] >= 1 and all(item["conversation_id"] in (primary, secondary) for item in messages["data"])


def _v1_token(lead: Lead) -> TestClient:
    """A client for the public API, signed with a token of the agency."""
    integration = lead.agency.post("/api/integrations", json={"name": "Merge tests", "scopes": ["inbox.read", "inbox.reply", "inbox.manage"]}).json()
    issued = lead.agency.post(f"/api/integrations/{integration['id']}/tokens", json={"expires_in_days": 30}).json()
    session = TestClient(lead.agency.app)
    session.headers["Authorization"] = f"Bearer {issued['token']}"
    return session


# --- lists hide the linked rows and aggregate the group -------------------------------------------


def test_lists_show_one_row_per_lead_with_the_group_aggregated(lead: Lead):
    primary = conv(lead, "widget", "widget:p", name="Maria", mode="human")
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="Maria WA", mode="human")
    third = conv(lead, "instagram", "ig-3", name="Maria IG", mode="human")
    say(primary, "first on the web", minute=1)
    say(secondary, "second on whatsapp", minute=2)
    say(third, "latest on instagram", minute=5)
    say(secondary, "another whatsapp", minute=4)
    lone = conv(lead, "widget", "widget:lone", name="Lone", mode="human")
    say(lone, "just me", minute=0)
    assert portal_merge(lead, primary, secondary).status_code == 200
    assert portal_merge(lead, primary, third).status_code == 200
    for path in ("conversations", "inbox"):
        response = lead.admin.get(f"{lead.base}/{path}")
        assert response.status_code == 200
        items = response.json() if path == "conversations" else response.json()["items"]
        ids = [item["id"] for item in items]
        assert secondary not in ids and third not in ids and primary in ids and lone in ids
        row = next(item for item in items if item["id"] == primary)
        assert row["channels"] == ["widget", "whatsapp", "instagram"] and row["linked_count"] == 2
        # Unread is summed over the group (all four visitor messages are unread) and the preview is the newest message.
        assert row["unread_count"] == 4 and row["unread"] is True
        assert row["preview"] == "latest on instagram"
        assert next(item for item in items if item["id"] == lone)["linked_count"] == 0
        assert next(item for item in items if item["id"] == lone)["channels"] == ["widget"]
    summary = lead.admin.get(f"{lead.base}/conversations/summary").json()
    assert summary["open"] == 3  # the seeded lead of the fixture, the merged lead, and the lone one
    inbox = lead.admin.get(f"{lead.base}/inbox").json()
    assert inbox["total"] == len(inbox["items"]) == 3
    # The agency's own lists read the same.
    agency_inbox = lead.agency.get("/api/conversations/inbox", params={"limit": 50}).json()
    ids = [item["id"] for item in agency_inbox]
    assert secondary not in ids and third not in ids
    merged_row = next(item for item in agency_inbox if item["id"] == primary)
    assert merged_row["channels"] == ["widget", "whatsapp", "instagram"] and merged_row["linked_count"] == 2
    assert merged_row["unread_count"] == 4 and merged_row["preview"] == "latest on instagram"
    plain = lead.agency.get("/api/conversations", params={"client_id": lead.id}).json()
    assert secondary not in [item["id"] for item in plain] and third not in [item["id"] for item in plain]
    assert next(item for item in plain if item["id"] == primary)["linked_count"] == 2


def test_reading_the_lead_reads_every_thread(lead: Lead):
    primary = conv(lead, "widget", "widget:p", name="P", mode="human")
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="S", mode="human")
    say(primary, "one", minute=1)
    say(secondary, "two", minute=2)
    assert portal_merge(lead, primary, secondary).status_code == 200
    assert next(item for item in portal_items(lead) if item["id"] == primary)["unread_count"] == 2
    # Opening the linked thread's id opens the lead: both threads are read.
    assert lead.admin.post(f"{lead.base}/conversations/{secondary}/read").status_code == 204
    assert next(item for item in portal_items(lead) if item["id"] == primary)["unread_count"] == 0
    assert state(primary).operator_read_at is not None and state(secondary).operator_read_at is not None


def test_a_lead_is_human_while_any_of_its_threads_is(lead: Lead):
    primary = conv(lead, "widget", "widget:p", name="P", mode="ai")
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="S", mode="ai")
    assert portal_merge(lead, primary, secondary).status_code == 200
    with TestingSession() as db:
        db.get(Conversation, uuid.UUID(secondary)).mode = "human"
        db.commit()
    assert next(item for item in portal_items(lead) if item["id"] == primary)["mode"] == "human"
    assert [item["id"] for item in portal_items(lead, mode="human")].count(primary) == 1
    assert primary not in [item["id"] for item in portal_items(lead, mode="ai")]
    assert lead.admin.get(f"{lead.base}/conversations/{primary}").json()["mode"] == "human"
    # Giving it back to the AI acts on the whole lead.
    assert lead.admin.patch(f"{lead.base}/conversations/{primary}/mode", json={"mode": "ai"}).json()["mode"] == "ai"
    assert state(secondary).mode == "ai"


def test_the_pipeline_and_reports_count_a_lead_once(lead: Lead):
    stage = lead.admin.post(f"{lead.base}/pipeline/stages", json={"name": "New", "color": "#3b82f6"}).json()
    primary = conv(lead, "widget", "widget:p", name="P", pipeline_stage_id=uuid.UUID(stage["id"]), deal_value=100)
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="S", pipeline_stage_id=uuid.UUID(stage["id"]), deal_value=50)
    say(primary, "hi", minute=1)
    say(secondary, "hola", minute=2)
    before = lead.admin.get(f"{lead.base}/pipeline/board").json()
    assert {card["id"] for card in before["cards"]} >= {primary, secondary}
    assert portal_merge(lead, primary, secondary).status_code == 200
    board = lead.admin.get(f"{lead.base}/pipeline/board").json()
    ids = [card["id"] for card in board["cards"]]
    assert primary in ids and secondary not in ids
    column = next(item for item in board["stages"] if item["id"] == stage["id"])
    assert column["conversation_count"] == 1 and column["deal_value_total"] == 100.0
    today = now_utc().date().isoformat()
    since = (now_utc().date() - timedelta(days=1)).isoformat()
    report = lead.admin.get(f"{lead.base}/reports", params={"from": since, "to": today}).json()
    assert report["started"] == 2 and report["open_now"] == 2  # the fixture lead and the merged lead
    # Message volume still counts every thread.
    assert report["inbound_messages"] == 2
    agency = lead.agency.get("/api/reports/operations", params={"from": since, "to": today}).json()
    assert agency["totals"]["conversations"] == 2 and agency["totals"]["inbound"] == 2
    assert lead.agency.get("/api/dashboard").json()["conversations"] == 2


def test_an_idle_merged_lead_resolves_as_one_and_only_when_every_thread_is_idle(lead: Lead):
    primary, secondary = merged_pair(lead)
    say(primary, "old", minute=-60 * 30)
    recent = say(secondary, "just now", minute=119)
    with TestingSession() as db:
        # A thread that was active a moment ago keeps the whole lead open.
        assert resolve_idle_ai_conversations(db, hours=24) == 0
    assert state(primary).status == "open" and state(secondary).status == "open"
    with TestingSession() as db:
        db.get(Message, uuid.UUID(recent)).created_at = BASE_TIME - timedelta(hours=40)
        db.commit()
        assert resolve_idle_ai_conversations(db, hours=24) == 1
    assert state(primary).status == "resolved" and state(secondary).status == "resolved"
    assert [item["event"] for item in activity(primary)][-1] == "auto_resolved"


# --- the detail ----------------------------------------------------------------------------------


def test_the_detail_is_one_timeline_that_says_where_each_message_came_from(lead: Lead):
    primary = conv(lead, "widget", "widget:p", name="Maria")
    secondary = conv(lead, "whatsapp", "573001112233@s.whatsapp.net", name="Maria WA")
    say(primary, "web 1", minute=1)
    say(secondary, "wa 1", minute=2)
    say(primary, "web 2", "human", minute=3)
    say(secondary, "wa 2", minute=4)
    assert portal_merge(lead, primary, secondary).status_code == 200
    for opened in (primary, secondary):
        detail = lead.admin.get(f"{lead.base}/conversations/{opened}")
        assert detail.status_code == 200
        body = detail.json()
        # Opening a linked thread's id answers with the primary's detail: it never shows as a lead of its own.
        assert body["id"] == primary and body["linked_count"] == 1 and body["channels"] == ["widget", "whatsapp"]
        exchanged = [item for item in body["messages"] if item["kind"] == "message"]
        assert [item["content"] for item in exchanged] == ["web 1", "wa 1", "web 2", "wa 2"]
        assert [item["channel"] for item in exchanged] == ["widget", "whatsapp", "widget", "whatsapp"]
        assert [item["conversation_id"] for item in exchanged] == [primary, secondary, primary, secondary]
        assert body["messages"][-1]["kind"] == "activity"  # the merge line, after everything said before it
        threads = body["linked_threads"]
        assert [(item["conversation_id"], item["is_primary"]) for item in threads] == [(primary, True), (secondary, False)]
        assert threads[0]["channel"] == "widget" and threads[0]["label"] == "Maria" and threads[0]["mode"] == "ai"
        assert threads[1]["channel"] == "whatsapp" and threads[1]["label"] == "+573001112233"
        assert set(threads[1]) >= {"conversation_id", "channel", "label", "account_label", "is_primary", "mode", "last_inbound_at"}
        assert threads[1]["last_inbound_at"] is not None
        # The reply goes out by default on the thread of the latest inbound message.
        assert body["reply_via_default"] == secondary
    agency = lead.agency.get(f"/api/conversations/{secondary}").json()
    assert agency["id"] == primary and [item["conversation_id"] for item in agency["messages"] if item["kind"] == "message"] == [
        primary, secondary, primary, secondary,
    ]
    assert agency["linked_count"] == 1 and agency["reply_via_default"] == secondary


def test_a_lead_without_threads_still_tells_its_channel(lead: Lead):
    alone = conv(lead, "widget", "widget:x", name="Alone")
    say(alone, "hi", minute=1)
    body = lead.admin.get(f"{lead.base}/conversations/{alone}").json()
    assert body["linked_count"] == 0 and body["channels"] == ["widget"]
    assert body["messages"][0]["channel"] == "widget" and body["messages"][0]["conversation_id"] == alone
    assert [item["conversation_id"] for item in body["linked_threads"]] == [alone] and body["reply_via_default"] == alone


def test_attachments_and_reactions_work_on_any_thread_of_the_lead(lead: Lead, monkeypatch):
    primary, secondary = merged_pair(lead)
    message = say(secondary, "look at this", minute=3, external_message_id="wamid-1")
    with TestingSession() as db:
        attachment = MessageAttachment(message_id=uuid.UUID(message), kind="image", mime="image/png", filename="a.png", size_bytes=3, data=b"png")
        db.add(attachment)
        db.commit()
        attachment_id = str(attachment.id)
    for opened in (primary, secondary):
        got = lead.admin.get(f"{lead.base}/conversations/{opened}/attachments/{attachment_id}")
        assert got.status_code == 200 and got.content == b"png"
    assert lead.agency.get(f"/api/conversations/{primary}/attachments/{attachment_id}").status_code == 200
    react = AsyncMock()
    monkeypatch.setattr("app.routers.portal.deliver_reaction", react)
    reacted = lead.admin.post(f"{lead.base}/conversations/{primary}/messages/{message}/reaction", json={"emoji": "👍"})
    assert reacted.status_code == 200, reacted.text
    # The reaction goes out on the thread the message lives on.
    assert str(react.call_args.args[1].id) == secondary
    assert next(item for item in reacted.json()["messages"] if item["id"] == message)["reaction"] == "👍"
    monkeypatch.setattr("app.routers.conversations.deliver_reaction", react)
    assert lead.agency.post(f"/api/conversations/{primary}/messages/{message}/reaction", json={"emoji": "❤️"}).status_code == 200
    assert str(react.call_args.args[1].id) == secondary
    # A message that belongs to no thread of the lead is not there.
    stranger = conv(lead, name="Stranger")
    foreign = say(stranger, "not yours", minute=1, external_message_id="wamid-2")
    assert lead.admin.post(f"{lead.base}/conversations/{primary}/messages/{foreign}/reaction", json={"emoji": "👍"}).status_code == 404
    assert lead.admin.get(f"{lead.base}/conversations/{primary}/attachments/{ZERO}").status_code == 404


# --- replying through a chosen thread --------------------------------------------------------------


def test_a_reply_goes_out_on_the_thread_the_caller_names(lead: Lead, monkeypatch):
    primary, secondary = merged_pair(lead)
    send = AsyncMock(side_effect=lambda *args, **kwargs: f"ext-{uuid.uuid4().hex}")
    monkeypatch.setattr("app.routers.portal.send_channel_message", send)
    default = lead.admin.post(f"{lead.base}/conversations/{primary}/reply", json={"content": "on the primary"})
    assert default.status_code == 200, default.text
    assert str(send.call_args.args[1].id) == primary
    via = lead.admin.post(f"{lead.base}/conversations/{primary}/reply",
                          json={"content": "on whatsapp", "via_conversation_id": secondary})
    assert via.status_code == 200, via.text
    assert str(send.call_args.args[1].id) == secondary
    stored = {item["content"]: item["conversation_id"] for item in via.json()["messages"]}
    assert stored["on the primary"] == primary and stored["on whatsapp"] == secondary
    via_primary = lead.admin.post(f"{lead.base}/conversations/{primary}/reply",
                                  json={"content": "explicit primary", "via_conversation_id": primary})
    assert via_primary.status_code == 200 and str(send.call_args.args[1].id) == primary
    # A thread of another lead, or nothing at all, is refused before anything is sent.
    calls = send.await_count
    outsider = conv(lead, name="Outsider")
    assert lead.admin.post(f"{lead.base}/conversations/{primary}/reply",
                           json={"content": "x", "via_conversation_id": outsider}).status_code == 404
    assert lead.admin.post(f"{lead.base}/conversations/{primary}/reply",
                           json={"content": "x", "via_conversation_id": ZERO}).status_code == 404
    assert send.await_count == calls
    # Replying through the linked thread's own id is acting on it: refused.
    assert lead.admin.post(f"{lead.base}/conversations/{secondary}/reply", json={"content": "x"}).status_code == 409


def test_other_reply_kinds_take_the_thread_too(lead: Lead, monkeypatch):
    primary, secondary = merged_pair(lead)
    media = AsyncMock(return_value="ext-media")
    monkeypatch.setattr("app.services.operator_media.send_channel_media", media)
    sent = lead.admin.post(
        f"{lead.base}/conversations/{primary}/reply-media",
        files={"file": ("note.txt", b"hello", "text/plain")}, data={"caption": "see", "via_conversation_id": secondary},
    )
    assert sent.status_code == 200, sent.text
    assert str(media.call_args.args[1].id) == secondary
    with TestingSession() as db:
        row = db.scalars(select(Message).where(Message.external_message_id == "ext-media")).one()
        assert str(row.conversation_id) == secondary
    bad = lead.admin.post(f"{lead.base}/conversations/{primary}/reply-media",
                          files={"file": ("n.txt", b"x", "text/plain")}, data={"via_conversation_id": ZERO})
    assert bad.status_code == 404
    # The agency door: text and location.
    text = AsyncMock(return_value="ext-2")
    monkeypatch.setattr("app.routers.conversations.send_channel_message", text)
    agency_reply = lead.agency.post(f"/api/conversations/{primary}/reply", json={"content": "hi", "via_conversation_id": secondary})
    assert agency_reply.status_code == 200 and str(text.call_args.args[1].id) == secondary
    assert lead.agency.post(f"/api/conversations/{primary}/reply", json={"content": "hi", "via_conversation_id": ZERO}).status_code == 404
    pin = AsyncMock(return_value="ext-pin")
    monkeypatch.setattr("app.routers.conversations.send_channel_location", pin)
    located = lead.agency.post(f"/api/conversations/{primary}/location",
                               json={"latitude": 4.6, "longitude": -74.0, "via_conversation_id": secondary})
    assert located.status_code == 200 and str(pin.call_args.args[1].id) == secondary
    assert lead.agency.post(f"/api/conversations/{primary}/location",
                            json={"latitude": 4.6, "longitude": -74.0, "via_conversation_id": ZERO}).status_code == 404
    with TestingSession() as db:
        assert str(db.scalars(select(Message).where(Message.external_message_id == "ext-pin")).one().conversation_id) == secondary


def test_the_public_api_reply_takes_the_thread_too(lead: Lead, monkeypatch):
    primary, secondary = merged_pair(lead)
    send = AsyncMock(return_value="ext-v1")
    monkeypatch.setattr("app.routers.api_v1.send_channel_message", send)
    token = _v1_token(lead)
    response = token.post(f"/api/v1/clients/{lead.id}/conversations/{primary}/reply",
                          json={"content": "via api", "via_conversation_id": secondary}, headers={"Idempotency-Key": "k-1"})
    assert response.status_code == 200, response.text
    assert str(send.call_args.args[1].id) == secondary and response.json()["id"] == primary
    assert token.post(f"/api/v1/clients/{lead.id}/conversations/{primary}/reply",
                      json={"content": "x", "via_conversation_id": ZERO}).status_code == 404
    assert token.post(f"/api/v1/clients/{lead.id}/conversations/{secondary}/reply", json={"content": "x"}).status_code == 409


# --- acting on the lead ----------------------------------------------------------------------------


def test_taking_control_assigning_resolving_and_archiving_act_on_the_whole_lead(lead: Lead):
    primary, secondary = merged_pair(lead)
    third = conv(lead, "instagram", "ig-7", name="Third")
    assert portal_merge(lead, primary, third).status_code == 200
    with TestingSession() as db:
        team = Team(client_id=uuid.UUID(lead.id), name="Sales")
        db.add(team)
        db.commit()
        team_id = str(team.id)
    base = f"{lead.base}/conversations/{primary}"
    assert lead.admin.patch(f"{base}/mode", json={"mode": "human"}).json()["mode"] == "human"
    assert state(secondary).mode == "human" and state(third).mode == "human"
    ana = lead.members["ana"]
    assert lead.admin.post(f"{base}/assignment", json={"assignee_id": ana}).status_code == 200
    assert str(state(secondary).assignee_id) == ana and str(state(third).assignee_id) == ana
    assert lead.admin.patch(f"{base}/team", json={"team_id": team_id}).status_code == 200
    assert str(state(secondary).team_id) == team_id and str(state(third).team_id) == team_id
    assert lead.admin.patch(f"{base}/status", json={"status": "resolved"}).json()["status"] == "resolved"
    assert state(secondary).status == "resolved" and state(third).status == "resolved" and state(secondary).resolved_at is not None
    # A resolved lead leaves the open list; the linked rows never show up on their own.
    assert primary not in [item["id"] for item in portal_items(lead, status="open")]
    assert primary in [item["id"] for item in portal_items(lead, status="resolved")]
    assert lead.admin.patch(f"{base}/archive", json={"archived": True}).status_code == 200
    assert state(secondary).archived_at is not None and state(third).archived_at is not None
    assert primary in [item["id"] for item in portal_items(lead, archived="true")]
    assert secondary not in [item["id"] for item in portal_items(lead, archived="true")]
    assert lead.admin.patch(f"{base}/archive", json={"archived": False}).status_code == 200
    assert state(secondary).archived_at is None
    assert lead.admin.patch(f"{base}/status", json={"status": "open"}).json()["status"] == "open"
    assert state(third).status == "open" and state(third).resolved_at is None
    assert lead.admin.patch(f"{base}/mode", json={"mode": "ai"}).json()["mode"] == "ai"
    assert state(secondary).mode == "ai" and state(secondary).assignee_id is None
    # Through the agency door as well.
    agency = f"/api/conversations/{primary}"
    assert lead.agency.patch(f"{agency}/mode", json={"mode": "human"}).status_code == 200
    assert state(third).mode == "human"
    assert lead.agency.patch(f"{agency}/status", json={"status": "resolved"}).status_code == 200
    assert state(third).status == "resolved"


def test_acting_directly_on_a_linked_thread_is_refused(lead: Lead):
    primary, secondary = merged_pair(lead)
    base = f"{lead.base}/conversations/{secondary}"
    stage = lead.admin.post(f"{lead.base}/pipeline/stages", json={"name": "New", "color": "#3b82f6"}).json()
    refused = [
        lead.admin.patch(f"{base}/mode", json={"mode": "human"}),
        lead.admin.patch(f"{base}/status", json={"status": "resolved"}),
        lead.admin.post(f"{base}/assignment", json={"assignee_id": lead.members["ana"]}),
        lead.admin.patch(f"{base}/archive", json={"archived": True}),
        lead.admin.patch(f"{base}/pipeline", json={"pipeline_stage_id": stage["id"]}),
        lead.admin.patch(f"{base}/lead", json={"custom_values": {}}),
        lead.admin.delete(base),
        lead.agency.patch(f"/api/conversations/{secondary}/mode", json={"mode": "human"}),
        lead.agency.patch(f"/api/conversations/{secondary}/status", json={"status": "resolved"}),
        lead.agency.patch(f"/api/conversations/{secondary}/lead", json={"custom_values": {}}),
        lead.agency.patch(f"/api/conversations/{secondary}/pipeline", json={"pipeline_stage_id": stage["id"]}),
    ]
    assert [response.status_code for response in refused] == [409] * len(refused), [r.text for r in refused]
    assert all("act on the lead" in response.json()["detail"] for response in refused)
    # Reading through it is fine: the lead card is the primary's.
    card = lead.admin.get(f"{base}/lead")
    assert card.status_code == 200 and card.json()["conversation_id"] == primary
    assert state(secondary).mode == state(primary).mode and state(secondary).status == "open"


def test_deleting_a_lead_deletes_its_threads_and_says_how_many(lead: Lead):
    primary, secondary = merged_pair(lead)
    say(secondary, "kept until the lead goes", minute=1)
    detail = lead.admin.get(f"{lead.base}/conversations/{primary}").json()
    assert detail["linked_count"] == 1
    assert lead.admin.delete(f"{lead.base}/conversations/{primary}").status_code == 409  # only from the archive
    assert lead.admin.patch(f"{lead.base}/conversations/{primary}/archive", json={"archived": True}).status_code == 200
    assert lead.admin.delete(f"{lead.base}/conversations/{secondary}").status_code == 409
    assert lead.admin.delete(f"{lead.base}/conversations/{primary}").status_code == 204
    with TestingSession() as db:
        assert db.get(Conversation, uuid.UUID(primary)) is None and db.get(Conversation, uuid.UUID(secondary)) is None
        assert db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(secondary))).all() == []


def test_archiving_resolved_and_deleting_archived_go_by_lead(lead: Lead):
    primary, secondary = merged_pair(lead)
    assert lead.admin.patch(f"{lead.base}/conversations/{primary}/status", json={"status": "resolved"}).status_code == 200
    archived = lead.admin.post(f"{lead.base}/conversations/archive-resolved")
    assert archived.status_code == 200 and archived.json()["count"] == 1
    assert state(secondary).archived_at is not None
    deleted = lead.admin.post(f"{lead.base}/conversations/delete-archived", json={})
    assert deleted.json()["count"] == 1
    with TestingSession() as db:
        assert db.get(Conversation, uuid.UUID(secondary)) is None


def test_a_status_change_stamps_the_lead_card(lead: Lead):
    primary, secondary = merged_pair(lead)
    card = lead.admin.get(f"{lead.base}/conversations/{primary}/lead").json()
    assert [(item["conversation_id"], item["is_primary"], item["channel"]) for item in card["linked_channels"]] == [
        (primary, True, "widget"), (secondary, False, "whatsapp"),
    ]
    assert card["linked_channels"][1]["label"] == "+573001112233" and "created_at" in card
    assert lead.agency.get(f"/api/conversations/{secondary}/lead").json()["conversation_id"] == primary


# --- candidates -------------------------------------------------------------------------------------


def _candidates(session: TestClient, url: str, **params) -> list[dict]:
    response = session.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_candidates_are_found_by_name_phone_email_and_number(lead: Lead):
    open_lead = conv(lead, "widget", "widget:open", name="Opened")
    accents = contact(lead, "José Núñez", phone="573101234567", email="jose@acme.test")
    joe = conv(lead, "whatsapp", "573101234567@s.whatsapp.net", name="Jose WA", contact_id=uuid.UUID(accents))
    other = conv(lead, "instagram", "ig-5", name="Someone Else")
    url = f"{lead.base}/leads/merge-candidates"
    ids = lambda **params: [item["conversation_id"] for item in _candidates(lead.admin, url, exclude=open_lead, **params)]  # noqa: E731
    assert joe in ids(q="jose") and other not in ids(q="jose")  # accent-insensitive
    assert ids(q="NÚÑEZ") == [joe] and ids(q="nunez") == [joe]
    assert ids(q="3101234") == [joe] and ids(q="+57 310 123") == [joe]
    assert ids(q="jose@acme") == [joe]
    number = state(other).number
    assert ids(q=str(number)) == [other] and ids(q=f"#{number}") == [other]
    assert open_lead not in ids()
    assert set(ids()) >= {joe, other}
    row = next(item for item in _candidates(lead.admin, url, q="jose", exclude=open_lead))
    assert row["conversation_id"] == joe and row["number"] == state(joe).number
    assert row["contact_name"] == "José Núñez" and row["phone"] == "573101234567" and row["email"] == "jose@acme.test"
    assert row["channel"] == "whatsapp" and row["channels"] == ["whatsapp"] and row["stage"] is None and row["deal_value"] is None
    assert row["created_at"]
    # The agency door answers the same.
    agency = _candidates(lead.agency, f"/api/clients/{lead.id}/leads/merge-candidates", q="nunez", exclude=open_lead)
    assert [item["conversation_id"] for item in agency] == [joe]


def test_candidates_are_primaries_only_and_never_the_lead_itself_or_its_threads(lead: Lead):
    primary, secondary = merged_pair(lead)
    other = conv(lead, "widget", "widget:o", name="Other lead")
    url = f"{lead.base}/leads/merge-candidates"
    for excluded in (primary, secondary):
        found = [item["conversation_id"] for item in _candidates(lead.admin, url, exclude=excluded)]
        assert primary not in found and secondary not in found and other in found
    # Without exclusion the linked thread is still not a lead to pick.
    found = [item["conversation_id"] for item in _candidates(lead.admin, url)]
    assert secondary not in found and primary in found
    merged = next(item for item in _candidates(lead.admin, url) if item["conversation_id"] == primary)
    assert merged["channels"] == ["widget", "whatsapp"]
    assert lead.admin.get(url, params={"exclude": ZERO}).status_code == 404
    assert lead.admin.get(url, params={"limit": 21}).status_code == 422


def test_candidates_are_limited_and_confined_to_the_client(lead: Lead, authenticated_client: TestClient):
    for index in range(25):
        conv(lead, "widget", f"widget:{index}", name=f"Lead {index}")
    url = f"{lead.base}/leads/merge-candidates"
    assert len(_candidates(lead.admin, url)) == 20
    other = Lead(authenticated_client, name="Other Co")
    conv(other, name="Zed Foreign")
    assert _candidates(lead.admin, url, q="zed") == []
    assert [item["contact_name"] for item in _candidates(other.admin, f"{other.base}/leads/merge-candidates", q="zed")] == ["Zed Foreign"]
    assert _candidates(lead.agency, f"/api/clients/{lead.id}/leads/merge-candidates", q="zed") == []


def test_a_lead_without_contact_is_found_by_the_phone_of_its_chat_and_shows_its_stage(lead: Lead):
    stage = lead.admin.post(f"{lead.base}/pipeline/stages", json={"name": "Hot", "color": "#ef4444"}).json()
    bare = conv(lead, "whatsapp", "573207654321@s.whatsapp.net", name="Push name", pipeline_stage_id=uuid.UUID(stage["id"]), deal_value=75)
    row = _candidates(lead.admin, f"{lead.base}/leads/merge-candidates", q="3207654")[0]
    assert row["conversation_id"] == bare and row["phone"] == "573207654321" and row["contact_name"] == "Push name"
    assert row["stage"] == {"id": stage["id"], "name": "Hot", "color": "#ef4444"} and row["deal_value"] == 75.0


# --- permissions, doors and gating -------------------------------------------------------------------


def test_merging_needs_contacts_manage_but_finding_candidates_only_the_inbox(lead: Lead):
    one = conv(lead, name="One")
    two = conv(lead, "whatsapp", "573000000020@s.whatsapp.net", name="Two")
    agent = lead.member("zoe", "agent")
    denied = portal_merge(lead, one, two, agent)
    assert denied.status_code == 403 and denied.json() == {"detail": "Your role cannot do this"}
    assert agent.get(f"{lead.base}/leads/merge-candidates").status_code == 200
    assert state(two).primary_conversation_id is None
    assert portal_merge(lead, one, two).status_code == 200
    assert TestClient(lead.agency.app).post(f"{lead.base}/leads/merge",
                                            json={"primary_conversation_id": one, "secondary_conversation_id": two}).status_code == 401
    assert TestClient(lead.agency.app).get(f"/api/clients/{lead.id}/leads/merge-candidates").status_code == 401


def test_a_portal_with_the_inbox_off_cannot_merge(lead: Lead):
    one = conv(lead, name="One")
    two = conv(lead, "whatsapp", "573000000021@s.whatsapp.net", name="Two")
    assert lead.agency.patch(f"/api/clients/{lead.id}/portal", json={"portal_features": {"inbox": False}}).status_code == 200
    assert portal_merge(lead, one, two).status_code == 403
    assert lead.admin.get(f"{lead.base}/leads/merge-candidates").status_code == 403
    # The agency panel is not gated by the client's portal functions.
    assert agency_merge(lead, one, two).status_code == 200


def test_the_agency_door_merges_and_reports_the_same_shape(lead: Lead):
    one = conv(lead, "widget", "widget:1", name="One", deal_value=10)
    two = conv(lead, "whatsapp", "573000000022@s.whatsapp.net", name="Two", deal_value=20)
    merged = agency_merge(lead, one, two)
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert set(body) == {"primary", "secondary_number"} and body["secondary_number"] == state(two).number
    assert body["primary"]["conversation_id"] == one and body["primary"]["deal_value"] == 10.0
    assert [item["channel"] for item in body["primary"]["linked_channels"]] == ["widget", "whatsapp"]
    assert activity(one)[-1]["event"] == "entity_merged"
    assert lead.agency.post(f"/api/clients/{lead.id}/leads/merge",
                            json={"primary_conversation_id": one, "secondary_conversation_id": one}).status_code == 409
    assert lead.agency.post(f"/api/clients/{lead.id}/leads/merge", json={"primary_conversation_id": one}).status_code == 422


def test_merging_a_resolved_primary_with_a_waiting_secondary_reopens_the_lead(lead: Lead):
    done = conv(lead, "widget", "widget:d", name="Done", status="resolved", resolved_at=now_utc())
    waiting = conv(lead, "whatsapp", "573000000023@s.whatsapp.net", name="Waiting", waiting_since=now_utc())
    assert portal_merge(lead, done, waiting).status_code == 200
    assert state(done).status == "open" and state(waiting).status == "open" and state(done).waiting_since is not None
    settled = conv(lead, "widget", "widget:e", name="Settled", status="resolved", resolved_at=now_utc())
    closed = conv(lead, "whatsapp", "573000000024@s.whatsapp.net", name="Closed", status="resolved", resolved_at=now_utc())
    assert portal_merge(lead, settled, closed).status_code == 200
    assert state(settled).status == "resolved" and state(closed).status == "resolved"


def test_the_migration_declares_the_link_column():
    from app.models import Conversation as Model

    column = Model.__table__.c.primary_conversation_id
    assert column.nullable and column.index and next(iter(column.foreign_keys)).ondelete == "CASCADE"
    assert PipelineStage.__tablename__ == "pipeline_stages"
