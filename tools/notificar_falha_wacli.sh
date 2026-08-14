#!/bin/sh
set -eu

. /root/.openclaw/secrets/whatsapp-health.env
: "${TELEGRAM_TOKEN:?TELEGRAM_TOKEN ausente}"
: "${TELEGRAM_CHAT:?TELEGRAM_CHAT ausente}"

/usr/bin/curl --fail --silent --show-error --max-time 20 \
  --request POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT}" \
  --data-urlencode "text=Falha persistente na captura do WhatsApp na VPS. O reparo automatico nao recuperou o wacli; verificar wey-whatsapp-live-sync.service." \
  >/dev/null
