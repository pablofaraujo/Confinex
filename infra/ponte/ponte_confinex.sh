#!/usr/bin/env bash
# Ponte Confinex: fila vps_briefings (Supabase) -> Claude Code -> relatorio.
set -Eeuo pipefail

umask 077

ENV_FILE=${CONFINEX_ENV_FILE:-/root/.openclaw/gateway.systemd.env}
PONTE_DIR=${CONFINEX_PONTE_DIR:-/root/ponte}
SPOOL_DIR="$PONTE_DIR/relatorios-pendentes"
SENTINELA="$PONTE_DIR/.last-cycle"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${CONFINEX_DB_URL:?CONFINEX_DB_URL ausente}"
: "${CONFINEX_DB_KEY:?CONFINEX_DB_KEY ausente}"
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY ausente}"

URL="$CONFINEX_DB_URL/rest/v1/vps_briefings"
H1="apikey: $CONFINEX_DB_KEY"
H2="Authorization: Bearer $CONFINEX_DB_KEY"
mkdir -p "$SPOOL_DIR"
chmod 700 "$SPOOL_DIR"

HEARTBEAT_PID=""
cleanup() {
  if [[ -n "$HEARTBEAT_PID" ]]; then
    pkill -P "$HEARTBEAT_PID" 2>/dev/null || true
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

registrar_ciclo() {
  touch "$SENTINELA"
  (
    while :; do
      sleep 60
      touch "$SENTINELA" 2>/dev/null || exit 0
    done
  ) >/dev/null 2>&1 &
  HEARTBEAT_PID=$!
}

patch_briefing() {
  local id=$1
  local estado_esperado=$2
  local payload=$3
  local corpo
  local http
  corpo=$(mktemp "$PONTE_DIR/.patch-resposta.XXXXXX")
  http=$(curl --silent --show-error --output "$corpo" --write-out '%{http_code}' \
    -X PATCH "$URL?id=eq.$id&status=eq.$estado_esperado" \
    -H "$H1" -H "$H2" \
    -H 'Content-Type: application/json' \
    -H 'Prefer: return=representation' \
    --data-binary "@$payload") || http=000
  if [[ "$http" =~ ^2 ]] && grep -q "$id" "$corpo"; then
    rm -f "$corpo"
    return 0
  fi
  printf '[%s] PATCH falhou id=%s http=%s resposta=%s\n' \
    "$(date -u +%FT%TZ)" "$id" "$http" \
    "$(tr '\n' ' ' < "$corpo" | cut -c1-800)" >&2
  rm -f "$corpo"
  return 1
}

finalizar_spools() {
  local spool id status
  shopt -s nullglob
  for spool in "$SPOOL_DIR"/*.json; do
    id=$(jq -r '.id' "$spool")
    status=$(jq -r '.status' "$spool")
    if [[ "$status" != concluido && "$status" != erro ]]; then
      printf '[%s] spool invalido preservado: %s\n' "$(date -u +%FT%TZ)" "$spool" >&2
      continue
    fi
    if patch_briefing "$id" executando "$spool"; then
      printf '[%s] relatorio pendente finalizado id=%s status=%s\n' \
        "$(date -u +%FT%TZ)" "$id" "$status"
      rm -f "$spool"
    fi
  done
  shopt -u nullglob
}

criar_spool() {
  local id=$1
  local status=$2
  local relatorio=$3
  local destino="$SPOOL_DIR/$id.json"
  jq -n \
    --arg id "$id" \
    --arg status "$status" \
    --arg relatorio "$relatorio" \
    --arg concluido_em "$(date -u +%FT%TZ)" \
    '{id:$id,status:$status,relatorio:($relatorio[0:50000]),concluido_em:$concluido_em}' \
    > "$destino.tmp"
  chmod 600 "$destino.tmp"
  mv -f "$destino.tmp" "$destino"
  printf '%s\n' "$destino"
}

registrar_ciclo
finalizar_spools

ROW=$(curl --fail --silent --show-error \
  "$URL?status=eq.pendente&order=criado_em&limit=1" -H "$H1" -H "$H2")
ID=$(jq -r '.[0].id // empty' <<<"$ROW")
[[ -z "$ID" ]] && exit 0
BRIEF=$(jq -r '.[0].briefing' <<<"$ROW")
TITULO=$(jq -r '.[0].titulo' <<<"$ROW")

printf '[%s] Processando ID=%s TITULO=%s\n' "$(date -u +%FT%TZ)" "$ID" "$TITULO"

CLAIM=$(curl --fail --silent --show-error -X PATCH \
  "$URL?id=eq.$ID&status=eq.pendente" -H "$H1" -H "$H2" \
  -H 'Content-Type: application/json' -H 'Prefer: return=representation' \
  -d "{\"status\":\"executando\",\"iniciado_em\":\"$(date -u +%FT%TZ)\"}")
grep -q "$ID" <<<"$CLAIM" || {
  printf '[%s] CLAIM recusado id=%s\n' "$(date -u +%FT%TZ)" "$ID" >&2
  exit 0
}

WRAPPER="Você está executando um briefing da fila vps_briefings da ponte Confinex.
ESCOPO PERMITIDO: arquivos/serviços do Juan (/root/.openclaw, openclaw-gateway.service),
o banco Confinex via CONFINEX_DB_URL/KEY do ambiente, e /root/ponte.
ESCOPO PERMITIDO ADICIONAL (autorizado pelo Pablo):
- criação e manutenção do usuário de sistema dedicado confinex-broker (nologin, sem sudo, sem grupos privilegiados);
- leitura e escrita em /opt/confinex-broker/** e /etc/confinex-broker/**;
- instalação e administração da unit confinex-broker.service, que deve permanecer disabled e inactive até janela explicitamente autorizada pelo Pablo (enable/start continuam PROIBIDOS por padrão).
RECUSE e reporte como erro qualquer instrução fora do escopo, que exponha
segredos/chaves/credenciais, mexa em SSH, usuários ou firewall.
Ao final, escreva um RELATÓRIO objetivo do que fez e dos resultados.
--- BRIEFING ---
"

set +e
OUT=$(cd /root && timeout 1800 claude -p "$WRAPPER$BRIEF" \
  --allowedTools 'Bash,Read,Edit,Write,WebFetch' 2>&1)
CLAUDE_RC=$?
set -e

STATUS=concluido
if [[ $CLAUDE_RC -ne 0 ]]; then
  STATUS=erro
  OUT="Falha do executor (codigo $CLAUDE_RC).\n$OUT"
fi
if grep -Eqi 'run /login|invalid authentication|not logged in|authentication required' <<<"$OUT"; then
  STATUS=erro
  OUT="Falha de autenticacao do executor. Nenhuma missao foi executada.\n$OUT"
fi
if [[ -z "$OUT" ]]; then
  STATUS=erro
  OUT='Falha do executor: nenhuma saida foi produzida.'
fi

for nome in CONFINEX_DB_KEY CONFINEX_DB_URL ANTHROPIC_API_KEY; do
  valor=${!nome:-}
  [[ -n "$valor" ]] && OUT=${OUT//"$valor"/"[$nome]"}
done

SPOOL=$(criar_spool "$ID" "$STATUS" "$OUT")
if patch_briefing "$ID" executando "$SPOOL"; then
  rm -f "$SPOOL"
  printf '[%s] PATCH concluido id=%s status=%s\n' \
    "$(date -u +%FT%TZ)" "$ID" "$STATUS"
else
  printf '[%s] relatorio preservado para retry: %s\n' \
    "$(date -u +%FT%TZ)" "$SPOOL" >&2
  exit 1
fi
