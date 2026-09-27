"""An agency renaming its own identifier keeps every old address working,
and a slug nobody retired can never be taken."""

from sqlalchemy import func, select

from app.models import Agency, AgencySlugAlias
from conftest import TestingSession
from test_platform_auth import _login, _platform_admin


def test_an_agency_rename_retires_the_old_slug(authenticated_client):
    client = authenticated_client  # first-run agency "Agencia Prisma", slug "agencia-prisma"
    _platform_admin()
    _login(client)
    renamed = client.patch("/api/agency", json={"slug": "prisma-renovada"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["slug"] == "prisma-renovada"
    with TestingSession() as db:
        assert db.scalar(select(AgencySlugAlias.id).where(AgencySlugAlias.slug == "agencia-prisma")) is not None
    resolved = client.get("/api/platform/agencies/by-slug/agencia-prisma")
    assert resolved.status_code == 200
    assert resolved.json()["slug"] == "prisma-renovada"


def test_a_rename_cannot_take_another_agencys_slug_or_alias(authenticated_client):
    client = authenticated_client
    with TestingSession() as db:
        other = Agency(name="Otra", slug="ocupada")
        db.add(other)
        db.flush()
        db.add(AgencySlugAlias(slug="retirada", agency_id=other.id))
        db.commit()
    assert client.patch("/api/agency", json={"slug": "ocupada"}).status_code == 409
    assert client.patch("/api/agency", json={"slug": "retirada"}).status_code == 409


def test_every_rename_is_kept_as_an_alias(authenticated_client):
    client = authenticated_client
    _platform_admin()
    _login(client)
    assert client.patch("/api/agency", json={"slug": "prisma-b"}).status_code == 200
    assert client.patch("/api/agency", json={"slug": "prisma-c"}).status_code == 200
    with TestingSession() as db:
        agency = db.scalar(select(Agency).where(Agency.name == "Agencia Prisma"))
        aliases = db.scalar(
            select(func.count()).select_from(AgencySlugAlias).where(AgencySlugAlias.agency_id == agency.id)
        )
        assert aliases == 2
    for retired in ("agencia-prisma", "prisma-b"):
        assert client.get(f"/api/platform/agencies/by-slug/{retired}").status_code == 200
