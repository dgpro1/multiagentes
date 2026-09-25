"""Conversation lifecycle: status changes, reply timing and the activity
events that narrate them inside the thread.

``mode`` (who answers) and ``status`` (where the case stands) are kept apart
on purpose: resolving does not hand the conversation back to the AI, and
taking control does not reopen a resolved case. What links them is the
activity trail, so a person reading the thread sees both.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from ..models import Conversation, Message, PortalUser, now_utc
from . import lead_group


STATUSES = ("open", "resolved")

# English fallbacks for clients that do not translate events themselves.
_ACTIVITY_TEXT = {
    "answered_from_phone": "{actor} replied from the phone; the AI is temporarily paused",
    "resumed_after_phone": "The phone pause ended; the AI can reply again",
    "routed_by_tag": "Routed to {target}: the contact is tagged {tag}",
    "resolved": "{actor} resolved the conversation",
    "reopened": "{actor} reopened the conversation",
    "reopened_by_contact": "Reopened: the contact wrote again",
    "taken_over": "{actor} took over the conversation",
    "returned_to_ai": "{actor} returned the conversation to the AI",
    "auto_resolved": "Resolved automatically after {hours} h without activity",
    "self_assigned": "{actor} is now handling the conversation",
    "assigned": "{actor} assigned the conversation to {assignee}",
    "transferred": "{actor} transferred the conversation to {assignee}",
    "unassigned": "{actor} released the conversation",
    "started": "{actor} started the conversation",
    "team_assigned": "{actor} moved the conversation to {team}",
    "team_removed": "{actor} took the conversation out of {team}",
    "pipeline_stage_changed": "{actor} moved the deal to {stage}",
    "pipeline_stage_removed": "{actor} took the deal out of the pipeline",
    "escalated": "{actor} escalated the conversation to {target}: {reason}",
    "archived": "{actor} archived the conversation",
    "unarchived": "{actor} restored the conversation from the archive",
    "blocked": "{actor} blocked the contact",
    "unblocked": "{actor} unblocked the contact; messages sent while blocked were not answered",
    "entity_merged": "{actor} merged lead #{secondary_number} into lead #{primary_number}",
    "appointment_created": "{actor} scheduled {title} on {date}",
    "appointment_rescheduled": "{actor} rescheduled {title} to {date}",
    "appointment_cancelled": "{actor} cancelled appointment {title}",
}


def record_activity(
    db: Session, conversation: Conversation, event: str, *, actor: str | None = None, details: dict | None = None
) -> Message:
    """Append an activity event to the thread. Never sent out, never fed to the model."""
    text = _ACTIVITY_TEXT[event].format(actor=actor or "Someone", **(details or {}))
    message = Message(
        conversation_id=conversation.id,
        role="system",
        kind="activity",
        activity={"event": event, **(details or {})},
        content=text,
        sender_type="system",
        sender_name=actor,
    )
    db.add(message)
    conversation.updated_at = now_utc()
    return message


def _threads(db: Session, conversation: Conversation) -> list[Conversation]:
    """The threads linked to a lead, for state that acts on the whole lead. A
    thread addressed directly (a channel event) acts on itself only."""
    if lead_group.is_linked(conversation):
        return []
    return lead_group.linked_threads(db, conversation)


def _mirror_status(db: Session, conversation: Conversation, status: str, now: datetime) -> None:
    for thread in _threads(db, conversation):
        thread.status = status
        thread.status_changed_at = now
        if status == "resolved":
            from .phone_handover import cancel_phone_pause

            cancel_phone_pause(thread)
            thread.resolved_at = now
            thread.waiting_since = None
        else:
            thread.resolved_at = None


def set_status(db: Session, conversation: Conversation, status: str, *, actor: str | None = None) -> bool:
    """Move the conversation to ``status`` if it is not there already.

    Returns whether anything changed, so callers can skip a commit and avoid
    a duplicate activity line when a button is pressed twice.
    """
    if status not in STATUSES:
        raise ValueError(f"Unknown conversation status: {status}")
    if conversation.status == status:
        return False
    now = now_utc()
    conversation.status = status
    conversation.status_changed_at = now
    _mirror_status(db, conversation, status, now)
    if status == "resolved":
        from .phone_handover import cancel_phone_pause

        cancel_phone_pause(conversation)
        conversation.resolved_at = now
        conversation.waiting_since = None
        record_activity(db, conversation, "resolved", actor=actor)
        from .outbound_webhooks import CONVERSATION_RESOLVED, emit

        db.flush()
        emit(
            db, agency_id=conversation.agency_id, client_id=conversation.client_id,
            event=CONVERSATION_RESOLVED,
            data={"conversation_id": str(conversation.id), "number": conversation.number, "channel": conversation.channel},
        )
        db.flush()
    else:
        conversation.resolved_at = None
        record_activity(db, conversation, "reopened", actor=actor)
    return True


def set_archived(db: Session, conversation: Conversation, archived: bool, *, actor: str | None = None) -> bool:
    """Move the conversation in or out of the archive.

    Archiving closes the case first when it is still open: an archived
    conversation is history, nobody answers it. Restoring brings it back as
    resolved, not open; the contact's next message opens a new one anyway.
    """
    if bool(conversation.archived_at) == archived:
        return False
    stamp = now_utc() if archived else None
    if archived:
        set_status(db, conversation, "resolved", actor=actor)
    conversation.archived_at = stamp
    for thread in _threads(db, conversation):
        thread.archived_at = stamp
    record_activity(db, conversation, "archived" if archived else "unarchived", actor=actor)
    return True


def set_mode(
    db: Session, conversation: Conversation, mode: str, *, actor: str | None = None, user: PortalUser | None = None
) -> bool:
    """Switch who answers, and leave a trace of it in the thread.

    Taking over from the portal also hands the conversation to that person;
    giving it back to the AI releases it, since nobody is handling it now.
    """
    from .phone_handover import cancel_phone_pause
    threads = _threads(db, conversation)
    rows = [conversation, *threads]
    # A lead is answered by a person while its primary or any thread is.
    if all(row.mode == mode and row.phone_pause_until is None for row in rows):
        return False
    now = now_utc()
    for row in rows:
        cancel_phone_pause(row)
        row.mode = mode
        if mode == "human":
            row.taken_over_at = now
            if user:
                row.assignee_id = user.id
                row.assigned_at = now
        else:
            row.assignee_id = None
            row.assigned_at = None
    record_activity(db, conversation, "taken_over" if mode == "human" else "returned_to_ai", actor=actor)
    return True


def assign(
    db: Session,
    conversation: Conversation,
    assignee: PortalUser | None,
    *,
    actor: str | None = None,
    actor_user: PortalUser | None = None,
) -> bool:
    """Hand the conversation to ``assignee`` (None releases it).

    Assigning a person means a person answers, so the AI steps aside. The
    thread says what happened in the words people use: took it, assigned
    it, transferred it, released it.
    """
    from .phone_handover import cancel_phone_pause
    timed = conversation.phone_pause_until is not None and assignee is not None
    if timed:
        cancel_phone_pause(conversation)
    new_id = assignee.id if assignee else None
    if not timed and conversation.assignee_id == new_id and (assignee is None or conversation.mode == "human"):
        return False
    previous = conversation.assignee
    now = now_utc()
    for row in (conversation, *_threads(db, conversation)):
        row.assignee_id = new_id
        row.assigned_at = now if assignee else None
        if assignee and row.mode != "human":
            row.mode = "human"
            row.taken_over_at = now
    if assignee is None:
        event, details = "unassigned", None
    elif actor_user and assignee.id == actor_user.id:
        event, details = "self_assigned", {"assignee": assignee.name}
    elif previous and previous.id != assignee.id:
        event, details = "transferred", {"assignee": assignee.name, "from": previous.name}
    else:
        event, details = "assigned", {"assignee": assignee.name}
    record_activity(db, conversation, event, actor=actor, details=details)
    return True


def set_team(
    db: Session,
    conversation: Conversation,
    team,
    *,
    actor: str | None = None,
) -> bool:
    """Move the conversation into ``team`` (None takes it out of its tray).

    Moving trays does not by itself touch mode or assignee; the routing
    service decides who picks it up. It does drop an assignee who is not a
    member of the new team, the way a physical tray would.
    """
    new_id = team.id if team else None
    if conversation.team_id == new_id:
        return False
    previous = conversation.team
    # Assign the relationship, not the id: callers keep reading
    # ``conversation.team`` in the same transaction (routing does).
    conversation.team = team
    for thread in _threads(db, conversation):
        thread.team = team
        if team is not None and thread.assignee_id and thread.assignee_id not in {
            member.portal_user_id for member in team.members
        }:
            thread.assignee_id = None
            thread.assigned_at = None
    if team is None:
        record_activity(
            db, conversation, "team_removed", actor=actor, details={"team": previous.name if previous else ""}
        )
        return True
    if conversation.assignee_id and conversation.assignee_id not in {
        member.portal_user_id for member in team.members
    }:
        conversation.assignee_id = None
        conversation.assigned_at = None
    record_activity(db, conversation, "team_assigned", actor=actor, details={"team": team.name})
    return True


def set_pipeline_stage(
    db: Session,
    conversation: Conversation,
    stage,
    *,
    deal_value=...,
    actor: str | None = None,
) -> bool:
    """Move the conversation's deal into ``stage`` (None takes it off the
    pipeline). ``deal_value`` is left untouched unless a caller passes one
    (including explicit ``None`` to clear it) — the sentinel default tells
    "no change" apart from "clear it", the way ``stage=None`` cannot.
    """
    new_id = stage.id if stage else None
    stage_changed = conversation.pipeline_stage_id != new_id
    value_changed = deal_value is not ... and conversation.deal_value != deal_value
    if not stage_changed and not value_changed:
        return False
    previous = conversation.pipeline_stage
    conversation.pipeline_stage = stage
    if value_changed:
        conversation.deal_value = deal_value
    if not stage_changed:
        conversation.updated_at = now_utc()
        return True
    if stage is None:
        record_activity(db, conversation, "pipeline_stage_removed", actor=actor,
                         details={"stage": previous.name if previous else ""})
        _emit_deal_moved(db, conversation, None, actor)
        return True
    record_activity(db, conversation, "pipeline_stage_changed", actor=actor, details={"stage": stage.name})
    _emit_deal_moved(db, conversation, stage, actor)
    return True


def _emit_deal_moved(db, conversation: Conversation, stage, actor: str | None) -> None:
    from .outbound_webhooks import DEAL_MOVED, emit

    db.flush()
    emit(
        db, agency_id=conversation.agency_id, client_id=conversation.client_id, event=DEAL_MOVED,
        data={
            "conversation_id": str(conversation.id),
            "number": conversation.number,
            "pipeline_stage_id": str(stage.id) if stage else None,
            "stage_name": stage.name if stage else None,
            "deal_value": float(conversation.deal_value) if conversation.deal_value is not None else None,
            "actor": actor,
        },
    )
    db.flush()


def note_inbound(db: Session, conversation: Conversation) -> None:
    """A contact wrote: they are waiting, and a resolved case is open again.

    A message on a thread that was merged into another lead lands on that lead:
    it is the lead that waits, reopens and comes back from the archive."""
    now = now_utc()
    if conversation.waiting_since is None:
        conversation.waiting_since = now
    if not lead_group.is_linked(conversation):
        if conversation.status == "resolved":
            conversation.status = "open"
            conversation.status_changed_at = now
            conversation.resolved_at = None
            record_activity(db, conversation, "reopened_by_contact")
        return
    lead = lead_group.primary_of(conversation)
    if lead.waiting_since is None:
        lead.waiting_since = now
    lead.updated_at = now
    if lead.status == "resolved" or conversation.status == "resolved" or lead.archived_at is not None:
        lead.archived_at = None
        for row in (lead, *_threads(db, lead)):
            row.archived_at = None
            row.status = "open"
            row.status_changed_at = now
            row.resolved_at = None
        record_activity(db, lead, "reopened_by_contact")


def note_reply(conversation: Conversation) -> None:
    """Something answered the contact, whether the AI or a person."""
    now = now_utc()
    for row in {id(item): item for item in (conversation, lead_group.primary_of(conversation))}.values():
        if row.first_reply_at is None:
            row.first_reply_at = now
        row.waiting_since = None


def resolve_idle_ai_conversations(db: Session, *, hours: float, now: datetime | None = None) -> int:
    """Resolve open AI-handled conversations idle for ``hours``.

    Idle means no exchanged message from either side, so a case the AI
    answered and the contact never followed up on leaves the open list on
    its own. Human-held conversations are left alone on purpose: the person
    who took them over is the only one who closes them.
    """
    if hours <= 0:
        return 0
    cutoff = (now or now_utc()) - timedelta(hours=hours)
    # A merged lead is idle only when every thread of it is.
    thread = aliased(Conversation)
    last_message_at = (
        select(func.max(Message.created_at))
        .join(thread, thread.id == Message.conversation_id)
        .where(
            or_(thread.id == Conversation.id, thread.primary_conversation_id == Conversation.id),
            Message.kind == "message",
        )
        .correlate(Conversation)
        .scalar_subquery()
    )
    idle = db.scalars(
        select(Conversation).where(
            lead_group.is_lead_row(),
            Conversation.status == "open",
            Conversation.mode == "ai",
            ~lead_group.has_human_thread(),
            func.coalesce(last_message_at, Conversation.created_at) < cutoff,
        )
    ).all()
    shown = int(hours) if float(hours).is_integer() else hours
    for conversation in idle:
        stamp = now_utc()
        for row in (conversation, *_threads(db, conversation)):
            row.status = "resolved"
            row.status_changed_at = stamp
            row.resolved_at = stamp
            row.waiting_since = None
        record_activity(db, conversation, "auto_resolved", details={"hours": shown})
    if idle:
        db.commit()
    return len(idle)


def exchanged_only(query):
    """Restrict a Message query to what was exchanged with the contact."""
    return query.where(Message.kind == "message")
