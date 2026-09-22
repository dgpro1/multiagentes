"""Messages with hidden phone numbers must still reach the inbox and get replies."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import Contact, ContactIdentity, Conversation, Message, WhatsAppCloudChannel
from app.services import messaging_provider as provider_client
from app.services import whatsapp_cloud, whatsapp_inbound
from app.services.ai import Completion
from app.services.contacts import normalize_phone
from app.services.whatsapp_identity import resolve_peer_contact
from conftest import TestingSession
from test_whatsapp_cloud import _event, _message, _post_signed, _setup_channel

BSUID = "CO.123456789012345Ab"
PHONE = "573001112233"


def incoming(mid, *, sender_id=BSUID, phone=None, name="Maria", text="Hello", conversation="conv-hidden"):
    sender = {"id": sender_id, "name": name}
    if phone:
        sender["phoneNumber"] = f"+{phone}"
    return {**_message(mid, text, conversation_id=conversation),
            "sender": sender}


def setup(client, monkeypatch):
    customer, agent, channel = _setup_channel(client)
    monkeypatch.setattr(whatsapp_inbound, "run_completion", AsyncMock(return_value=Completion(text="Welcome!")))
    monkeypatch.setattr(whatsapp_cloud, "send_text", AsyncMock(side_effect=["wamid.reply", "wamid.reply-2"]))
    monkeypatch.setattr(provider_client, "mark_read", AsyncMock(return_value=None))
    monkeypatch.setattr(provider_client, "send_typing", AsyncMock(return_value=None))
    return customer, agent, channel


@pytest.mark.parametrize("sender_id", [BSUID, "CO.ENT.123456789012345Ab"])
def test_hidden_phone_receives_replies_and_deduplicates(authenticated_client, monkeypatch, sender_id):
    _customer, _agent, channel = setup(authenticated_client, monkeypatch)
    payload = _event("acct-1", incoming("wamid.hidden", sender_id=sender_id), event_id="evt-h1")
    assert _post_signed(authenticated_client, payload).status_code == 200
    assert _post_signed(authenticated_client, payload).status_code == 200
    with TestingSession() as db:
        conv = db.scalar(select(Conversation))
        assert conv.external_chat_id == sender_id and conv.contact_name == "Maria"
        assert conv.contact.phone is None
        assert conv.provider_conversation_id == "conv-hidden"
        assert db.scalar(select(ContactIdentity)).external_user_id == sender_id
        messages = db.scalars(select(Message).order_by(Message.created_at)).all()
        assert [m.sender_type for m in messages] == ["visitor", "ai"]
        assert messages[-1].external_message_id == "wamid.reply"
        send = whatsapp_cloud.send_text
        assert send.await_count == 1
        assert send.call_args.args[:2] == ("acct-1", "conv-hidden")


@pytest.mark.parametrize("phone_first", [True, False])
def test_identity_transitions_keep_one_case_and_the_phone(authenticated_client, monkeypatch, phone_first):
    _customer, _agent, channel = setup(authenticated_client, monkeypatch)
    first = incoming("wamid.in-0", sender_id=PHONE if phone_first else BSUID,
                     phone=PHONE if phone_first else None)
    second = incoming("wamid.in-1", sender_id=BSUID if phone_first else PHONE,
                      phone=None if phone_first else PHONE)
    assert _post_signed(authenticated_client, _event("acct-1", first, event_id="evt-t0")).status_code == 200
    assert _post_signed(authenticated_client, _event("acct-1", second, event_id="evt-t1")).status_code == 200
    with TestingSession() as db:
        assert db.scalar(select(Conversation).where(Conversation.provider_conversation_id == "conv-hidden")) is not None
        assert len(db.scalars(select(Conversation)).all()) == 1
        conv = db.scalar(select(Conversation))
        assert conv.contact.phone == PHONE


def test_same_user_id_is_scoped_to_client_and_account(authenticated_client):
    _, _, data = _setup_channel(authenticated_client)
    with TestingSession() as db:
        channel = db.get(WhatsAppCloudChannel, uuid.UUID(data["id"]))
        first = resolve_peer_contact(db, channel, BSUID)
        channel.external_account_id = "another-account"
        second = resolve_peer_contact(db, channel, BSUID)
        assert first.id != second.id
        assert first.phone is None and second.phone is None


def test_hidden_numbers_are_not_phones(authenticated_client):
    assert normalize_phone(BSUID) is None
    assert normalize_phone("CO.ENT.123456789012345Ab") is None
