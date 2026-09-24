import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .config import get_settings
from .database import new_session
from .services.conversation_state import resolve_idle_ai_conversations
from .routers import (    agency,
    agent_tools,
    agents,
    api_v1,
    auth,
    calendar,
    catalog,
    clients,
    conversations,
    dashboard,
    domains,
    industries,
    integrations,
    messaging_webhook,
    mobile,
    oauth,
    pipeline,
    portal,
    portal_manage,
    providers,
    reports,
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

AUTO_RESOLVE_SWEEP_SECONDS = 15 * 60


async def _auto_resolve_loop() -> None:
    """Close idle AI conversations on a timer, for as long as the app runs."""
    while True:
        await asyncio.sleep(AUTO_RESOLVE_SWEEP_SECONDS)
        try:
            with new_session() as db:
                closed = resolve_idle_ai_conversations(db, hours=get_settings().auto_resolve_after_hours)
            if closed:
                logger.info("Auto-resolved %d idle AI conversation(s)", closed)
        except Exception:  # noqa: BLE001 - a failed sweep must not stop the next one
            logger.exception("Auto-resolve sweep failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from .services.social_worker import start_worker, stop_worker
    start_worker()
    sweeper = asyncio.create_task(_auto_resolve_loop()) if settings.auto_resolve_after_hours > 0 else None
    asyncio.create_task(_ensure_messaging_webhook())
    asyncio.create_task(_restore_evolution_channels())
    try:
        yield
    finally:
        await stop_worker()
        if sweeper:
            sweeper.cancel()


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
        await provider.ensure_webhook("OpenLivery inbox", provider.webhook_url(), secret, provider.INBOX_EVENTS)
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
    title="OpenLivery API",
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
app.include_router(clients.router, prefix="/api")
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
app.include_router(pipeline.router, prefix="/api")
app.include_router(social_webhook.public_router, prefix="/api")
