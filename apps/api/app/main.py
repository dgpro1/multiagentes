import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .config import get_settings
from .database import each_database, new_session
from .services.conversation_state import resolve_idle_ai_conversations
from .routers import (    agency,
    agency_backend,
    agent_tools,
    agents,
    api_v1,
    appointments,
    auth,
    calendar,
    catalog,
    channel_quotas,
    data_store,
    clients,
    conversations,
    dashboard,
    domains,
    industries,
    integrations,
    lead_card,
    messaging_webhook,
    mobile,
    oauth,
    pipeline,
    platform,
    platform_auth,
    platform_invitations,
    portal,
    portal_manage,
    professionals,
    providers,
    reports,
    resources,
    services,
    whatsapp,
    whatsapp_cloud,
    whatsapp_evolution,
    widget,
    webchat,
    social,
    social_webhook,
)


settings = get_settings()
logger = logging.getLogger(__name__)

# Deleting a central row a client's own database points at clears it there too.
from .services import tenant_references  # noqa: E402

tenant_references.install()

AUTO_RESOLVE_SWEEP_SECONDS = 15 * 60


async def _auto_resolve_loop() -> None:
    """Close idle AI conversations on a timer, for as long as the app runs."""
    while True:
        await asyncio.sleep(AUTO_RESOLVE_SWEEP_SECONDS)
        try:
            closed = 0
            with new_session() as db:
                for _client in each_database(db):
                    closed += resolve_idle_ai_conversations(db, hours=get_settings().auto_resolve_after_hours)
            if closed:
                logger.info("Auto-resolved %d idle AI conversation(s)", closed)
        except Exception:  # noqa: BLE001 - a failed sweep must not stop the next one
            logger.exception("Auto-resolve sweep failed")


async def _attachment_offload_loop(interval: int) -> None:
    """Move chat attachments to each connected client's own bucket, in batches."""
    from .services.attachment_offload import offload_batch

    def sweep() -> int:
        with new_session() as db:
            return offload_batch(db)

    while True:
        await asyncio.sleep(interval)
        try:
            # boto3 is synchronous: the sweep runs off the event loop.
            moved = await asyncio.to_thread(sweep)
            if moved:
                logger.info("Moved %d attachment(s) to client buckets", moved)
        except Exception:  # noqa: BLE001 - a failed sweep must not stop the next one
            logger.exception("Attachment offload sweep failed")


async def _update_client_schemas() -> None:
    """At boot: bring every connected client database to this release's tenant
    schema, then replay what was kept for clients that were behind."""
    from .services.tenant_schema import upgrade_all
    from .services.tenant_switch import replay_pending

    def run() -> list:
        with new_session() as db:
            return upgrade_all(db)

    try:
        for client_id in await asyncio.to_thread(run):
            await replay_pending(client_id)
    except Exception:  # noqa: BLE001 - a client database must never block the boot
        logger.exception("Client schema update failed")


KEPT_WEBHOOK_RETRY_SECONDS = 60


async def _retry_kept_webhooks_loop() -> None:
    """Replay webhooks kept while a client's own database was not answering."""
    from .services.tenant_switch import retry_kept

    while True:
        await asyncio.sleep(KEPT_WEBHOOK_RETRY_SECONDS)
        try:
            replayed = await retry_kept()
            if replayed:
                logger.info("Replayed %d kept webhook(s)", replayed)
        except Exception:  # noqa: BLE001 - a failed sweep must not stop the next one
            logger.exception("Kept webhook retry failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from .services.social_worker import start_worker, stop_worker
    start_worker()
    # Every task this boot starts is held and cancelled on shutdown. Three of
    # them write to the database (the Evolution restore commits per channel, the
    # tenant upgrade_all migrates), and a task that outlives the app keeps
    # writing to a database that has already moved on: under uvicorn --reload
    # they accumulate, and the test suite boots the app once per test.
    background = [
        asyncio.create_task(_ensure_messaging_webhook()),
        asyncio.create_task(_restore_evolution_channels()),
        asyncio.create_task(_update_client_schemas()),
        asyncio.create_task(_retry_kept_webhooks_loop()),
    ]
    if settings.auto_resolve_after_hours > 0:
        background.append(asyncio.create_task(_auto_resolve_loop()))
    offload_interval = settings.attachment_offload_interval_seconds
    if offload_interval > 0:
        background.append(asyncio.create_task(_attachment_offload_loop(offload_interval)))
    try:
        yield
    finally:
        await stop_worker()
        for task in background:
            task.cancel()
        # Awaited, not just cancelled: the loop may not outlive this context
        # (a test's TestClient closes right after the block), so a task that has
        # not finished cancelling would keep running against a dead database.
        await asyncio.gather(*background, return_exceptions=True)


async def _ensure_messaging_webhook() -> None:
    """Register the shared provider event subscription, once per boot.
    Best-effort: a missing key or network only logs, never stops the app."""
    try:
        from .services import messaging_provider as provider

        if not provider.configured():
            return
        secret = get_settings().messaging_provider_webhook_secret.strip()
        if not secret:
            logger.warning("Messaging webhook secret is missing; event deliveries stay unverified")
            return
        await provider.ensure_webhook("HunterAI inbox", provider.webhook_url(), secret, provider.INBOX_EVENTS)
    except Exception:
        logger.exception("Messaging webhook registration failed")


async def _restore_evolution_channels() -> None:
    """Reconnect the WhatsApp QR lines driven by Evolution after a boot.
    Best-effort: a down Evolution only logs, the lines keep their stored
    state."""
    from .services import evolution as evolution_driver

    if not evolution_driver.enabled():
        return
    try:
        from .models import WhatsAppChannel

        with new_session() as db:
            channels = db.scalars(
                select(WhatsAppChannel).where(
                    WhatsAppChannel.is_enabled.is_(True),
                    WhatsAppChannel.status.in_(("connected", "reconnecting", "connecting", "qr")),
                )
            ).all()
            ids = [channel.id for channel in channels]
        for channel_id in ids:
            with new_session() as db:
                channel = db.get(WhatsAppChannel, channel_id)
                if channel:
                    await evolution_driver.restore_channel(channel)
                    db.commit()
    except Exception:  # noqa: BLE001 - restore must never block the boot
        logger.exception("Evolution channel restore failed")


app = FastAPI(
    title="HunterAI API",
    description=(
        "API to manage agencies, clients and AI agents. Third parties build "
        "on the versioned public API under /api/v1 (see docs/en/api.md); "
        "the panel routes answer cookie sessions and scoped API tokens alike."
    ),
    version="0.3.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    # The configured frontend origin (a real domain in production) plus any
    # localhost/127.0.0.1 port, so changing WEB_PORT never breaks local dev.
    allow_origins=[settings.frontend_url],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok"}


@app.exception_handler(HTTPException)
async def v1_http_exception_handler(request: Request, exc: HTTPException):
    return await api_v1.http_exception_response(request, exc)


@app.exception_handler(RequestValidationError)
async def v1_validation_exception_handler(request: Request, exc: RequestValidationError):
    return await api_v1.validation_exception_response(request, exc)


app.include_router(auth.router, prefix="/api")
app.include_router(agency.router, prefix="/api")
app.include_router(agency_backend.router, prefix="/api")
app.include_router(clients.router, prefix="/api")
app.include_router(channel_quotas.router, prefix="/api")
app.include_router(industries.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(api_v1.router, prefix="/api")
app.include_router(oauth.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(webchat.router, prefix="/api")
app.include_router(agent_tools.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(conversations.client_router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(mobile.router, prefix="/api")
app.include_router(portal_manage.router, prefix="/api")
app.include_router(portal.router, prefix="/api")
app.include_router(whatsapp.router, prefix="/api")
if get_settings().whatsapp_internal_api:
    app.include_router(whatsapp.internal_router, prefix="/api")
app.include_router(whatsapp_evolution.public_router, prefix="/api")
app.include_router(whatsapp_cloud.router, prefix="/api")
app.include_router(messaging_webhook.public_router, prefix="/api")
app.include_router(messaging_webhook.router, prefix="/api")
app.include_router(widget.router, prefix="/api")
app.include_router(domains.public_router, prefix="/api")
app.include_router(social.router, prefix="/api")
app.include_router(calendar.router, prefix="/api")
app.include_router(professionals.router, prefix="/api")
app.include_router(services.router, prefix="/api")
app.include_router(resources.router, prefix="/api")
app.include_router(data_store.router, prefix="/api")
app.include_router(appointments.router, prefix="/api")
app.include_router(lead_card.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(platform_auth.router, prefix="/api")
app.include_router(platform.router, prefix="/api")
app.include_router(platform_invitations.router, prefix="/api")
app.include_router(social_webhook.public_router, prefix="/api")
