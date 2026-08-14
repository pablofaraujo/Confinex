#!/bin/sh
set -u

WACLI_BIN="${WACLI_BIN:-/usr/local/bin/wacli}"
WACLI_STORE="${WACLI_STORE:-/root/.local/state/wacli-confinex}"
UNIDADE="${UNIDADE:-wey-whatsapp-live-sync.service}"
MAX_ESPERAS="${MAX_ESPERAS:-24}"
INTERVALO="${INTERVALO:-5}"

tentativa=1
while [ "$tentativa" -le "$MAX_ESPERAS" ]; do
  diagnostico=$("$WACLI_BIN" --store "$WACLI_STORE" --read-only --json doctor 2>/dev/null || true)
  if printf '%s' "$diagnostico" | /usr/bin/python3 -c \
    'import json,sys; d=json.load(sys.stdin).get("data") or {}; raise SystemExit(0 if not d.get("lock_held") else 1)' \
    2>/dev/null; then
    systemctl reset-failed "$UNIDADE" 2>/dev/null || true
    systemctl --no-block start "$UNIDADE"
    exit 0
  fi
  /usr/bin/sleep "$INTERVALO"
  tentativa=$((tentativa + 1))
done

echo "O store do wacli nao foi liberado para retomar a captura continua." >&2
exit 1
