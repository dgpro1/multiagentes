from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


APP_DIR = Path(__file__).resolve().parents[1]   # apps/api
REPO_ROOT = Path(__file__).resolve().parents[3]  # monorepo root (used for a shared local .env)


class Settings(BaseSettings):
    app_name: str = "OpenLivery API"
    database_url: str = "postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery"
    secret_key: str = "dev-local-change-this-key-please"
    encryption_key: str = "dev-local-change-this-key-too"
    frontend_url: str = "http://localhost:3000"
    access_token_minutes: int = 60 * 24 * 7
    # Session cookie flags. Defaults suit local HTTP; set cookie_secure=true (and
    # cookie_samesite=none when the frontend and API are on different sites)
    # behind HTTPS in production.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    # Rate limiting on public/unauthenticated endpoints (per client IP). Disable
    # only for tests or when a proxy in front already enforces limits.
    rate_limit_enabled: bool = True
    # Requests per second allowed to each API token, counted per token (Kommo's
    # public limit is 7). Over it, the token answers 429 with retry_after.
    api_token_rate_limit_per_second: int = 7
    # SSRF guard for agent HTTP tools: URLs resolving to private/loopback
    # addresses are rejected. Enable only on self-hosted deployments that need
    # tools to reach internal services.
    tools_allow_private_urls: bool = False
    storage_dir: Path = APP_DIR / "storage"
    backend_url: str = "http://localhost:8000"
    # Auth for the whatsapp routers internal endpoints (X-Bridge-Token header) —
    # inbound/reaction/etc. under /internal/whatsapp, used by the test suite as
    # a driver-agnostic way to simulate WhatsApp events.
    whatsapp_bridge_token: str = "dev-local-change-this-bridge-token"
    # Evolution API (https://docs.evolutionfoundation.com.br) — the WhatsApp QR
    # driver. A WhatsApp QR line needs url and key set; without them, WhatsApp
    # QR is simply unavailable (WhatsApp API/Cloud and social channels are
    # unaffected).
    evolution_api_url: str = ""
    evolution_api_key: str = ""
    # Shared secret Evolution sends in the webhook Authorization header.
    evolution_webhook_secret: str = ""
    # Absolute URL Evolution calls with events. Inside Docker this is the API
    # service (http://api:8000/...); outside it is the public origin of the
    # deployment. Empty falls back to backend_url for local development.
    evolution_webhook_url: str = ""
    # Speech-to-text models offered for the audio capability. OpenRouter serves
    # them through its audio endpoint but lists them nowhere its API exposes,
    # so the offer is declared here; every other model is read live.
    transcription_models: str = "openai/gpt-4o-mini-transcribe,openai/gpt-4o-transcribe,openai/gpt-transcribe"
    # Unified messaging provider (WhatsApp API, Instagram, Messenger).
    # One server key covers every channel; each channel keeps only the
    # provider-side account id. Webhook deliveries are signed with the
    # webhook secret (HMAC-SHA256 over the raw body).
    messaging_provider_api_key: str = ""
    messaging_provider_base_url: str = "https://zernio.com/api/v1"
    messaging_provider_webhook_secret: str = ""
    # Public HTTPS origin for the provider callback and event webhook.
    # Empty falls back to social_public_url, then frontend_url.
    messaging_provider_public_url: str = ""
    # Official messaging APIs. App credentials remain on the server.
    social_graph_version: str = "v25.0"
    social_worker_enabled: bool = True
    social_worker_interval_seconds: float = 2.0
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_webhook_verify_token: str = ""
    instagram_human_agent_enabled: bool = False
    messenger_app_id: str = ""
    messenger_app_secret: str = ""
    messenger_webhook_verify_token: str = ""
    messenger_human_agent_enabled: bool = False
    messenger_login_config_id: str = ""
    # Public HTTPS origin; defaults to frontend_url when left empty.
    social_public_url: str = ""
    social_oauth_state_minutes: int = 10

    # Google Calendar. One OAuth client (a "Web application" in Google Cloud)
    # serves the whole installation; each person on a client's team authorizes
    # their own calendar through it. The redirect URI must be registered on
    # that OAuth client exactly as used; empty means
    # {frontend_url}/api/calendar/oauth/callback, which is right behind the
    # gateway. Connection links stay valid for calendar_link_days.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    calendar_link_days: int = 7

    # Off by default: conversations no longer resolve themselves. When set,
    # conversations the AI is answering resolve after this many hours without
    # a message from either side; conversations a person took over are never
    # closed automatically. 0 disables.
    auto_resolve_after_hours: float = 0.0

    # Push notifications for the mobile app. "none" (the default) sends nothing
    # and needs no account with anyone; "webhook" POSTs each event to
    # push_webhook_url so you can route it through whatever you already use.
    # Deployments may register further providers at startup — see
    # app/services/notifications.py and docs/push-notifications.md.
    push_provider: str = "none"
    push_webhook_url: str = ""
    push_webhook_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", APP_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
