import re
import unicodedata
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Agency, AgencySlugAlias, Client


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:140] or "espacio"


def slug_free(db: Session, slug: str) -> bool:
    """Whether no agency (current or renamed) holds this identifier. An alias
    is a retired slug that still resolves to its agency, so a new agency may
    not take it."""
    if db.scalar(select(Agency.id).where(Agency.slug == slug)):
        return False
    if db.scalar(select(AgencySlugAlias.id).where(AgencySlugAlias.slug == slug)):
        return False
    return True


def unique_agency_slug(db: Session, value: str) -> str:
    base = slugify(value)
    candidate = base
    for _ in range(100):
        if slug_free(db, candidate):
            return candidate
        candidate = f"{base}-{str(uuid.uuid4())[:6]}"
    return f"{base}-{uuid.uuid4().hex[:12]}"


def unique_slug(db: Session, model: type[Agency] | type[Client], field_name: str, value: str) -> str:
    base = slugify(value)
    candidate = base
    for _ in range(100):
        field = getattr(model, field_name)
        if not db.scalar(select(model.id).where(field == candidate)):
            return candidate
        candidate = f"{base}-{str(uuid.uuid4())[:6]}"
    return f"{base}-{uuid.uuid4().hex[:12]}"
