#!/bin/sh
set -u

WACLI_BIN="${WACLI_BIN:-/usr/local/bin/wacli}"
WACLI_STORE="${WACLI_STORE:-/root/.local/state/wacli-confinex}"
MAX_TENTATIVAS="${MAX_TENTATIVAS:-3}"
ESPERA_RETRY="${ESPERA_RETRY:-20}"

tentativa=1
while [ "$tentativa" -le "$MAX_TENTATIVAS" ]; do
  if /usr/bin/timeout --signal=TERM --kill-after=30s 5m \
    "$WACLI_BIN" --store "$WACLI_STORE" sync --once --idle-exit 45s \
    --presence-mode quiet --max-db-size 2GB --max-reconnect 2m; then
    exit 0
  fi
  if [ "$tentativa" -lt "$MAX_TENTATIVAS" ]; then
    echo "Sincronizacao falhou na tentativa ${tentativa}; nova tentativa sera feita." >&2
    /usr/bin/sleep "$ESPERA_RETRY"
  fi
  tentativa=$((tentativa + 1))
done

echo "Sincronizacao falhou apos ${MAX_TENTATIVAS} tentativas." >&2
exit 1
