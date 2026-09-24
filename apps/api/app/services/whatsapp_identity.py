"""Keep opaque user identifiers distinct from telephone numbers."""

import re

_USER_ID = re.compile(r"[A-Z]{2}\.(?:ENT\.)?[A-Za-z0-9]{1,128}\Z")


def is_user_id(value: str | None) -> bool:
    return isinstance(value, str) and bool(_USER_ID.fullmatch(value))


def user_id(message: dict, direction: str = "from") -> str | None:
    for key in (f"{direction}_user_id", f"{direction}_parent_user_id"):
        if is_user_id(message.get(key)):
            return message[key]
    return None


def resolve_peer_contact(db, channel, peer: str, *, name=None, sender_user_id=None):
    from .contacts import find_contact, phone_from_chat_id, resolve_contact

    phone = phone_from_chat_id(peer)
    identity = sender_user_id or (peer if is_user_id(peer) else None)
    if identity:
        contact = resolve_contact(db, channel.client_id, provider="whatsapp",
            external_account_id=channel.external_account_id or str(channel.id),
            external_user_id=identity, phone=phone, name=name)
        if phone and not contact.phone and not find_contact(db, channel.client_id, phone):
            contact.phone = phone
            db.flush()
        return contact
    return resolve_contact(db, channel.client_id, phone=phone, name=name) if phone else None
