"""A client's own details: identity fields and the logo.

Shared by the agency's client page and the client portal's Details screen,
which edit the same row from two doors. Only the fields the portal may change
pass through ``apply_details``; activation, portal settings, the custom domain
and the agency link are never written here.
"""

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import industries
from ..models import Client

MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}


def check_industry(industry: str, business_type: str) -> None:
    error = industries.validate(industry, business_type)
    if error:
        raise HTTPException(status_code=422, detail=error)


def apply_details(db: Session, client: Client, values: dict) -> None:
    """Write ``values`` (only the keys the caller sent) to ``client`` and commit.

    Changing the industry drops a business type that no longer belongs to it.
    An explicit null is ignored: every one of these columns must hold a value.
    """
    values = {key: value for key, value in values.items() if value is not None}
    industry = values.get("industry", client.industry)
    business_type = values.get("business_type", client.business_type)
    if "industry" in values and "business_type" not in values and industries.get_type(industry, business_type) is None:
        values["business_type"] = ""
        business_type = ""
    check_industry(industry, business_type)
    for key, value in values.items():
        setattr(client, key, value)
    db.commit()


async def store_logo(db: Session, client: Client, file: UploadFile) -> None:
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(status_code=400, detail="Use a PNG, JPG, WebP or SVG logo")
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="The logo exceeds the 2 MB limit")
    client.logo_data = data
    client.logo_mime = file.content_type
    db.commit()


def clear_logo(db: Session, client: Client) -> None:
    client.logo_data = None
    client.logo_mime = None
    db.commit()
