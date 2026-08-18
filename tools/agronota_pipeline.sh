#!/usr/bin/env bash
# Pipeline idempotente do AgroNota usado na VPS. Não promove dado operacional.
set -u

_ENV=/root/.openclaw/gateway.systemd.env
CONFINEX_DB_URL=$(grep -E '^CONFINEX_DB_URL=' "$_ENV" | cut -d= -f2-)
CONFINEX_DB_KEY=$(grep -E '^CONFINEX_DB_KEY=' "$_ENV" | cut -d= -f2-)
export CONFINEX_DB_URL CONFINEX_DB_KEY

LOOKBACK_DAYS="${AGRONOTA_LOOKBACK_DAYS:-30}"
# A varredura diária precisa reconciliar toda a janela baixada. As execuções
# incrementais continuam podendo reduzir a janela com RECONCILE_SINCE_DAYS=3.
RECON_SINCE_DAYS="${RECONCILE_SINCE_DAYS:-$LOOKBACK_DAYS}"
BIN=/root/.openclaw/workspace/skills/agronota/bin
LOGDIR=/var/log/cfagro
mkdir -p "$LOGDIR"
STAMP=$(date -u +%FT%TZ)

echo "[$STAMP] === agronota-pipeline start (lookback=${LOOKBACK_DAYS}d reconcile=${RECON_SINCE_DAYS}d) ==="

python3 "$BIN/download_new_nfs.py" --lookback-days "$LOOKBACK_DAYS" \
    2> >(tee -a "$LOGDIR/agronota_pipeline.err" >&2) \
    | tee "$LOGDIR/agronota_pipeline_download.jsonl"
DL_RC=${PIPESTATUS[0]}

python3 "$BIN/reconcile_new_nfs.py" --since-days "$RECON_SINCE_DAYS" \
    2> >(tee -a "$LOGDIR/agronota_pipeline.err" >&2) \
    | tee "$LOGDIR/agronota_pipeline_reconcile.jsonl"
RC_RC=${PIPESTATUS[0]}

MONITOR_ARGS=(--executar --confirmacao "PROCESSAR NFS AGRONOTA PARA REVISAO")
if [[ -n "${AGRONOTA_MONITOR_DRY_RUN:-}" ]]; then
    MONITOR_ARGS=()
fi

python3 /root/ponte/tools/monitorar_agronota.py \
    --xml-store /root/.openclaw/workspace/skills/agronota/xml_store \
    --since-days "$RECON_SINCE_DAYS" \
    "${MONITOR_ARGS[@]}" \
    2> >(tee -a "$LOGDIR/agronota_pipeline.err" >&2) \
    | tee "$LOGDIR/agronota_pipeline_monitor.jsonl"
MON_RC=${PIPESTATUS[0]}

STAMP=$(date -u +%FT%TZ)
echo "[$STAMP] === agronota-pipeline done (dl=$DL_RC reconcile=$RC_RC monitor=$MON_RC) ==="
exit $(( DL_RC | RC_RC | MON_RC ))
