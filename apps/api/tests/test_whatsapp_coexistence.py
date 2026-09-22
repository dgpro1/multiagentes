"""Companion-app machinery after the direct-sync retirement.

Numbers run through the unified provider, always Cloud-API-only. The old
event fields are acknowledged and ignored, stale sync rows drain instead
of stalling, and the pure guards (window, reply requirement, locks) keep
working for rows that still carry the legacy flag.
"""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.models import Conversation, WhatsAppCloudChannel, WhatsAppCoexistenceEvent, new_uuid, now_utc
from app.services import whatsapp_coexistence as coex
from conftest import TestingSession
from test_whatsapp_cloud import _setup_channel


def legacy_row(authenticated_client):
    _, _, data = _setup_channel(authenticated_client)
    with TestingSession() as db:
        channel = db.get(WhatsAppCloudChannel, uuid.UUID(data["id"]))
        channel.coexistence = True
        channel.status = "connected"
        channel.is_enabled = True
        db.commit()
    return uuid.UUID(data["id"])


def test_retired_fields_are_acknowledged_and_ignored(authenticated_client):
    channel_id = legacy_row(authenticated_client)
    with TestingSession() as db:
        channel = db.get(WhatsAppCloudChannel, channel_id)
        assert coex.accept_change(db, channel, "history", {"threads": []}, waba_id="waba-1") is False
        assert coex.accept_change(db, channel, "smb_message_echoes", {}, waba_id="waba-1") is False
        assert db.scalars(select(WhatsAppCoexistenceEvent)).all() == []


def test_stale_sync_rows_drain_as_processed(authenticated_client):
    channel_id = legacy_row(authenticated_client)
    with TestingSession() as db:
        db.add(WhatsAppCoexistenceEvent(id=new_uuid(), channel_id=channel_id, event_key="k",
            field="history", payload={}, cursor=0, attempts=7, available_at=now_utc(), created_at=now_utc()))
        db.commit()
    import asyncio

    with TestingSession() as db:
        assert asyncio.run(coex.process_pending(db)) == 1
        row = db.scalar(select(WhatsAppCoexistenceEvent))
        assert row.processed_at is not None


def test_reply_guards_stay_for_legacy_rows(authenticated_client):
    channel_id = legacy_row(authenticated_client)
    with TestingSession() as db:
        channel = db.get(WhatsAppCloudChannel, channel_id)
        conversation = Conversation(agency_id=channel.agency_id, client_id=channel.client_id,
            agent_id=channel.agent_id, channel="whatsapp_cloud", whatsapp_cloud_channel_id=channel.id,
            external_chat_id="111", status="open", mode="ai", title="Case",
            social_last_inbound_at=now_utc() - timedelta(hours=25))
        db.add(conversation)
        db.commit()
        fields = coex.window_fields(conversation)
        assert fields["reply_window_open"] is False
        assert fields["reply_block_reason"] == "reply_window_closed"
        try:
            coex.require_reply(conversation)
            raised = False
        except Exception:
            raised = True
        assert raised
        channel.coexistence = False
        coex.require_reply(conversation)


def test_refresh_syncs_linked_numbers_and_flags_missing_authorization(authenticated_client, monkeypatch):
    import asyncio

    from app.services import whatsapp_cloud as cloud_service

    channel_id = legacy_row(authenticated_client)
    monkeypatch.setattr(cloud_service, "verify_account",
                        AsyncMock(return_value={"display_phone_number": "+1", "verified_name": "Shop",
                                                "quality_rating": "GREEN", "messaging_limit": "TIER_1K", "username": "+1"}))
    with TestingSession() as db:
        channel = db.get(WhatsAppCloudChannel, channel_id)
        channel.external_account_id = "acct-1"
        db.commit()
        asyncio.run(coex.refresh_connection(db, channel))
        assert channel.display_name == "Shop"
    with TestingSession() as db:
        channel = db.get(WhatsAppCloudChannel, channel_id)
        channel.external_account_id = ""
        channel.status = "connected"
        channel.is_enabled = True
        db.commit()
        asyncio.run(coex.refresh_connection(db, channel))
        assert channel.status == "disconnected"


def test_manual_routes_work_on_legacy_rows(authenticated_client):
    channel_id = legacy_row(authenticated_client)
    with TestingSession() as db:
        stored = db.get(WhatsAppCloudChannel, channel_id)
        client_id, agent_id = str(stored.client_id), str(stored.agent_id)
    url = f"/api/whatsapp-cloud/channels/{client_id}"
    assert authenticated_client.put(url, json={"agent_id": agent_id}).status_code == 200
    assert authenticated_client.post(f"{url}/disconnect").status_code == 200
