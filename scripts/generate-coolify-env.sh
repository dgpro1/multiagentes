#!/bin/sh
# Prints the environment variables for docker-compose.coolify.yml with fresh random
# secrets, ready to paste into Coolify (Environment Variables -> Developer view).
# Nothing is written to disk. Usage: ./scripts/generate-coolify-env.sh app.example.com
set -eu

domain="${1:-}"
if [ -z "$domain" ]; then
  echo "Usage: $0 <public domain, e.g. app.example.com>" >&2
  exit 1
fi

hex() { openssl rand -hex "$1"; }

cat <<ENV
FRONTEND_URL=https://$domain
POSTGRES_PASSWORD=$(hex 24)
SECRET_KEY=$(hex 32)
ENCRYPTION_KEY=$(hex 32)
MESSAGING_PROVIDER_WEBHOOK_SECRET=$(hex 32)
EVOLUTION_API_KEY=$(hex 24)
EVOLUTION_WEBHOOK_SECRET=$(hex 32)
EVOLUTION_DB_PASSWORD=$(hex 24)
COOKIE_SECURE=true
# Optional: the messaging provider key (WhatsApp API, Instagram, Messenger).
MESSAGING_PROVIDER_API_KEY=
# Optional: Google Calendar. Register https://$domain/api/calendar/oauth/callback
# as an authorized redirect URI in Google Cloud, then fill these three.
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://$domain/api/calendar/oauth/callback
ENV
echo "" >&2
echo "Save ENCRYPTION_KEY somewhere safe: it decrypts the stored AI keys and WhatsApp sessions and must never change." >&2
