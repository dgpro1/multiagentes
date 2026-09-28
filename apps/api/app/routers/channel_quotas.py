"""The agency's line quotas across its clients.

One call for the planning screen: every channel type with the plan and what is
connected, and every client with what it uses and what it was assigned. The
per-client assignment lives next to the client itself
(``PUT /clients/{id}/channel-allowances``), because that is where it is edited.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..api_scopes import CHANNELS_READ
from ..database import get_db
from ..deps import confined_client_id, get_current_user, require
from ..models import Agency, User
from ..schemas import ChannelQuotaMatrix
from .. import channel_quotas

router = APIRouter(prefix="/channel-quotas", tags=["Channel quotas"])


@router.get("", response_model=ChannelQuotaMatrix, dependencies=[Depends(require(CHANNELS_READ))])
def quota_matrix(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The agency's whole distribution of lines, in one call, so the planning
    table is not one request per client. A token confined to a single client sees
    that client's row and nothing else."""
    return channel_quotas.matrix(db, db.get(Agency, user.agency_id), confined_client_id(user))
