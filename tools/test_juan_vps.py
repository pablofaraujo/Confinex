#!/usr/bin/env python3
"""Verificador efêmero do Juan executado pelo test_ecossistema.py na VPS."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import urllib.request
from pathlib import Path
from typing import Any


CONFIG = globals().get("CONFIG_TESTE", {})
BASE = Path("/root/juan-severino")
HANDLERS = BASE / "handlers"
WORKSPACE = Path("/root/.openclaw/workspace")
SESSIONS = Path("/root/.openclaw/agents/juan/sessions")
ENV_FILE = Path("/root/.openclaw/gateway.systemd.env")
TABLES = (
    "abates",
    "compras",
    "contexto_handoff",
    "eventos",
    "memorias_agentes",
    "operation_drafts",
    "pending_actions",
    "pesagens_caderno",
    "vendas",
)
STATUS_EVENTO_VALIDOS = {"cancelado", "corrigido", "pendente", "registrado"}
FERRAMENTAS_MIDIA_INTERNAS = {"pdf", "image", "file_fetch"}


def carregar_env() -> dict[str, str]:
    env = dict(os.environ)
    try:
        env_text = ENV_FILE.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return env
    for line in env_text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env.setdefault(key, value)
    return env


ENV = carregar_env()
PYCACHE_TESTE = Path("/tmp") / f"confinex-pycache-{uuid.uuid4().hex}"
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
ENV["PYTHONPYCACHEPREFIX"] = str(PYCACHE_TESTE)


def run(
    command: list[str],
    *,
    timeout: int = 600,
    cwd: Path = BASE,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=ENV,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if process.returncode:
        raise RuntimeError(
            f"{' '.join(command)} falhou: "
            f"{(process.stderr or process.stdout).strip()[:1600]}"
        )
    return process


def json_output(command: list[str], *, timeout: int = 600) -> dict[str, Any]:
    process = run(command, timeout=timeout)
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"saída não JSON de {' '.join(command)}: {process.stdout[:1000]}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("saída JSON inesperada")
    return value


def snapshot() -> dict[str, dict[str, Any]]:
    base = ENV.get("CONFINEX_DB_URL", "").rstrip("/")
    key = ENV.get("CONFINEX_DB_KEY", "")
    if not base or not key:
        raise RuntimeError("credenciais de leitura do Confinex não disponíveis")
    output = {}
    for table in TABLES:
        rows = supabase_rows(table, "id")
        ids = [str(row["id"]) for row in rows]
        output[table] = {
            "quantidade": len(ids),
            "assinatura": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
        }
    return output


def supabase_rows(table: str, select: str) -> list[dict[str, Any]]:
    base = ENV["CONFINEX_DB_URL"].rstrip("/")
    key = ENV["CONFINEX_DB_KEY"]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        url = (
            f"{base}/rest/v1/{table}?select={select}&order=id.asc"
            f"&limit=1000&offset={offset}"
        )
        request = urllib.request.Request(
            url,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            lote = json.load(response)
        rows.extend(lote)
        if len(lote) < 1000:
            return rows
        offset += len(lote)


def validar_eventos() -> list[str]:
    rows = supabase_rows("eventos", "id,status")
    statuses = sorted({str(row.get("status")) for row in rows})
    invalidos = set(statuses) - STATUS_EVENTO_VALIDOS
    if invalidos:
        raise RuntimeError("status de evento inválido: " + ", ".join(invalidos))
    return statuses


def validar_prompt() -> None:
    effective = (WORKSPACE / "AGENTS.md").read_text(encoding="utf-8")
    skill = (WORKSPACE / "skills/pesagem-ocr/SKILL.md").read_text(encoding="utf-8")
    combined = effective + "\n" + skill
    for required in (
        "MediaPath",
        "MediaPaths",
        "media://",
        "arquivo_grupo_router.py",
        "file_fetch",
        "antes ou depois",
        "Falha técnica ao processar o anexo",
    ):
        if required not in combined:
            raise RuntimeError(f"instrução efetiva não contém {required}")
    texto = combined.lower()
    bloqueios_escrita = (
        "não salve",
        "nao salve",
        "nada foi salvo",
        "nenhuma leitura gera escrita",
        "nunca use `--criar-rascunho`",
    )
    if not any(regra in texto for regra in bloqueios_escrita):
        raise RuntimeError("instrução efetiva não bloqueia gravação automática")


def validar_contrato_roteador(source: str | None = None) -> None:
    """Impede regressão de extrato PDF para compra e de vínculos incompletos."""
    source = source or (HANDLERS / "arquivo_grupo_router.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "def parse_pdf_bank_statement",
        '"classe": "extrato_bancario"',
        '"importado": False',
        '"resultado": {"operation_draft_id": draft["id"]}',
        '"duplicado": True',
        "a mesma origem já existe com classificação diferente",
    ):
        if required not in source:
            raise RuntimeError(f"contrato do roteador não contém {required}")


def validar_arquivo(path_text: str, legenda: str, grupo_id: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_file():
        raise RuntimeError(f"arquivo real não encontrado: {path}")
    if path.suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}:
        raise RuntimeError(f"formato real não aceito: {path.suffix}")

    ocr = json_output(
        [sys.executable, str(HANDLERS / "compra_documento_ocr.py"), str(path)]
    )
    if ocr.get("ocr_origem") != "openclaw_openai":
        raise RuntimeError(f"OCR externo não foi usado para {path.suffix}")
    if ocr.get("eh_compra"):
        for field in ("vendedor", "quantidade"):
            if ocr.get(field) in (None, ""):
                raise RuntimeError(f"compra lida sem {field}")

    referencias = [str(path)]
    if path.parent == Path("/root/.openclaw/media/inbound"):
        referencias.extend(
            [
                f"media://inbound/{path.name}",
                f"media:/inbound/{path.name}",
                f"inbound/{path.name}",
            ]
        )
    rotas = []
    for referencia in referencias:
        route = json_output(
            [
                sys.executable,
                str(HANDLERS / "arquivo_grupo_router.py"),
                referencia,
                "--grupo-id",
                grupo_id,
                "--mensagem-id",
                f"teste-ecossistema-{uuid.uuid4().hex}",
                "--texto",
                legenda,
                "--dry-run",
            ]
        )
        if route.get("dry_run") is not True or "rascunho" in route:
            raise RuntimeError("roteador real não permaneceu em dry-run")
        if route.get("arquivo") != str(path.resolve()):
            raise RuntimeError(
                f"referência {referencia} não resolveu para o anexo real"
            )
        rotas.append(referencia)
    routed = route.get("routed") or {}
    if routed.get("classe") == "compra_extraida":
        dados = routed.get("dados") or {}
        if not isinstance(dados.get("campos_pendentes"), list):
            raise RuntimeError("compra não informou lista objetiva de pendências")
        if dados.get("valor_calculado") is None:
            faltantes = " ".join(dados.get("campos_pendentes") or [])
            if not any(
                item in faltantes
                for item in ("peso", "preco", "quantidade", "desconto")
            ):
                raise RuntimeError(
                    "compra ficou sem cálculo e sem explicar o dado necessário"
                )
    return {
        "formato": path.suffix.lower(),
        "classe": routed.get("classe"),
        "ocr": ocr.get("ocr_origem"),
        "rotas_validadas": rotas,
    }


def calls_from_trajectory(path: Path) -> list[dict[str, Any]]:
    calls = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool.call":
            calls.append(event.get("data") or {})
    return calls


def auditar_chamadas_midia(
    calls: list[dict[str, Any]],
    referencia: str,
    *,
    minimo_roteador: int = 1,
) -> dict[str, Any]:
    indices_roteador = []
    primeiro_indice_midia = None
    for index, call in enumerate(calls):
        name = str(call.get("name") or "")
        args = json.dumps(call.get("arguments") or {}, ensure_ascii=False)
        args_lower = args.lower()
        toca_midia = (
            referencia in args
            or "media://inbound/" in args_lower
            or name in FERRAMENTAS_MIDIA_INTERNAS
            or "arquivo_grupo_router.py" in args
        )
        if toca_midia and primeiro_indice_midia is None:
            primeiro_indice_midia = index
        if name in FERRAMENTAS_MIDIA_INTERNAS:
            raise RuntimeError(
                f"ferramenta interna de mídia usada na trajetória: {name}"
            )
        if any(
            token in args_lower
            for token in ("pdftotext", "pdftoppm", "pesagem-ocr.sh")
        ):
            raise RuntimeError("fallback interno de mídia usado na trajetória")
        if name == "bash" and "arquivo_grupo_router.py" in args:
            if "--dry-run" not in args:
                raise RuntimeError("roteador foi chamado fora de dry-run")
            indices_roteador.append(index)

    if len(indices_roteador) < minimo_roteador:
        raise RuntimeError(
            f"esperadas {minimo_roteador} chamadas do roteador; "
            f"encontradas {len(indices_roteador)}"
        )
    if primeiro_indice_midia != indices_roteador[0]:
        raise RuntimeError("a primeira ferramenta de mídia não foi o roteador")
    return {
        "primeira_ferramenta_midia": "arquivo_grupo_router.py",
        "chamadas_roteador": len(indices_roteador),
        "ferramentas_internas": [],
        "dry_run": True,
    }


def limpar_sessao(marker: str) -> None:
    for path in SESSIONS.iterdir():
        if path.is_file() and marker in path.name:
            path.unlink(missing_ok=True)
    restantes = [
        path.name
        for path in SESSIONS.iterdir()
        if path.is_file() and marker in path.name
    ]
    if restantes:
        raise RuntimeError("a sessão de teste não foi removida por completo")
    preview = json_output(
        [
            "openclaw",
            "sessions",
            "cleanup",
            "--agent",
            "juan",
            "--dry-run",
            "--fix-missing",
            "--json",
        ],
        timeout=120,
    )
    if preview.get("missing") != 1:
        raise RuntimeError(
            "a limpeza esperava exatamente uma referência ausente do teste"
        )
    if any(
        preview.get(campo)
        for campo in ("dmScopeRetired", "pruned", "capped")
    ) or (preview.get("unreferencedArtifacts") or {}).get("removedFiles"):
        raise RuntimeError("a limpeza de sessão atingiria itens fora do teste")
    json_output(
        [
            "openclaw",
            "sessions",
            "cleanup",
            "--agent",
            "juan",
            "--fix-missing",
            "--json",
        ],
        timeout=120,
    )
    store = SESSIONS / "sessions.json"
    if store.exists() and marker in store.read_text(
        encoding="utf-8",
        errors="ignore",
    ):
        raise RuntimeError("o índice de sessões manteve referência ao teste")


def validar_indice_sessoes() -> None:
    preview = json_output(
        [
            "openclaw",
            "sessions",
            "cleanup",
            "--agent",
            "juan",
            "--dry-run",
            "--fix-missing",
            "--json",
        ],
        timeout=120,
    )
    if preview.get("missing"):
        raise RuntimeError(
            "o índice de sessões já possui referências sem arquivo; "
            "limpe-as antes da bateria"
        )


def validar_pre_processamento_anthropic() -> None:
    """Garante que anexos grandes respeitem o limite visual do fallback."""
    source = """
import importlib.util
import io
import inspect
import json
import re
import tempfile
from pathlib import Path
from PIL import Image

handler_path = Path("/root/juan-severino/handlers/pesagem_caderno_ocr.py")
spec = importlib.util.spec_from_file_location("pesagem_caderno_ocr", handler_path)
if spec is None or spec.loader is None:
    raise RuntimeError("handler de pesagem não pôde ser carregado")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)
limite = int(handler.MAX_DIMENSAO_ANTHROPIC)
fonte_chamada = inspect.getsource(handler.call_anthropic_model)
tokens = re.search(r'"max_tokens"\\s*:\\s*(\\d+)', fonte_chamada)
if tokens is None or int(tokens.group(1)) < 8192:
    raise RuntimeError("limite de saída Anthropic insuficiente para PDF")
if "stop_reason" not in fonte_chamada or "max_tokens" not in fonte_chamada:
    raise RuntimeError("truncamento Anthropic não é tratado explicitamente")
with tempfile.TemporaryDirectory(prefix="confinex-anthropic-dimensao-") as tmp:
    entrada = Path(tmp) / "grande.jpg"
    Image.new("RGB", (limite + 501, 2), "white").save(entrada, "JPEG")
    conteudo, mime = handler.load_image_as_jpeg(entrada)
    with Image.open(io.BytesIO(conteudo)) as resultado:
        dimensao = max(resultado.size)
print(json.dumps({
    "mime": mime,
    "dimensao": dimensao,
    "limite": limite,
    "max_tokens": int(tokens.group(1)),
    "truncamento_tratado": True,
}))
"""
    processo = run([sys.executable, "-c", source], timeout=120)
    resultado = json.loads(processo.stdout)
    if resultado.get("mime") != "image/jpeg":
        raise RuntimeError("fallback Anthropic não normalizou a imagem para JPEG")
    if int(resultado.get("dimensao") or 0) > int(resultado.get("limite") or 0):
        raise RuntimeError("fallback Anthropic manteve dimensão acima do limite")
    if int(resultado.get("max_tokens") or 0) < 8192:
        raise RuntimeError("fallback Anthropic manteve limite de saída insuficiente")
    if resultado.get("truncamento_tratado") is not True:
        raise RuntimeError("fallback Anthropic não trata resposta truncada")


def validar_agente(
    path_text: str,
    legenda: str,
    grupo_id: str,
) -> dict[str, Any]:
    marker = f"teste-ecossistema-{uuid.uuid4().hex}"
    path = Path(path_text)
    referencia = (
        f"media://inbound/{path.name}"
        if path.parent == Path("/root/.openclaw/media/inbound")
        else path_text
    )
    mensagens = [
        f"[media attached: {referencia}]",
        f"[media attached: {referencia}]",
        f"MediaPath: {path_text} MediaPaths: {path_text}",
    ]
    try:
        for tentativa, anexo in enumerate(mensagens, start=1):
            message = (
                f"[Telegram grupo {grupo_id} mensagem {marker}-{tentativa}] "
                f"{legenda} {anexo} "
                "Teste técnico em dry-run: processe pelo fluxo normal, "
                "não entregue em canal externo e não grave nada."
            )
            run(
                [
                    "openclaw",
                    "agent",
                    "--agent",
                    "juan",
                    "--session-id",
                    marker,
                    "--message",
                    message,
                    "--thinking",
                    "low",
                    "--timeout",
                    "600",
                    "--json",
                ],
                timeout=700,
            )
        trajectory = SESSIONS / f"{marker}.trajectory.jsonl"
        if not trajectory.exists():
            matches = [
                p
                for p in SESSIONS.glob("*.trajectory.jsonl")
                if marker in p.read_text(encoding="utf-8", errors="ignore")
            ]
            if not matches:
                raise RuntimeError("trajetória do teste do agente não foi encontrada")
            trajectory = matches[0]
        calls = calls_from_trajectory(trajectory)
        return auditar_chamadas_midia(
            calls,
            referencia,
            minimo_roteador=3,
        )
    finally:
        limpar_sessao(marker)


def snapshot_cache_ocr() -> dict[Path, bytes]:
    cache = WORKSPACE / ".juan-ocr-jobs/cache"
    if not cache.exists():
        return {}
    return {path: path.read_bytes() for path in cache.glob("*.json")}


def limpar_temporarios(cache_antes: dict[Path, bytes]) -> None:
    cache = WORKSPACE / ".juan-ocr-jobs/cache"
    if cache.exists():
        for path in cache.glob("*.json"):
            if path not in cache_antes:
                path.unlink(missing_ok=True)
        for path, conteudo in cache_antes.items():
            if not path.exists() or path.read_bytes() != conteudo:
                path.write_bytes(conteudo)
    if PYCACHE_TESTE.exists():
        shutil.rmtree(PYCACHE_TESTE)


def main() -> int:
    before = snapshot()
    cache_antes = snapshot_cache_ocr()
    evidencias: list[dict[str, Any]] = []
    evidencias_agente: list[dict[str, Any]] = []
    try:
        py_files = sorted(str(path) for path in HANDLERS.glob("*.py"))
        run([sys.executable, "-m", "py_compile", *py_files])
        run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(HANDLERS),
                "-p",
                "test_*.py",
                "-v",
            ]
        )
        run(["openclaw", "config", "validate"])
        validar_indice_sessoes()
        validar_pre_processamento_anthropic()
        validar_contrato_roteador()
        for service in (
            "openclaw-gateway.service",
            "juan-compra-ocr-worker.service",
        ):
            state = run(["systemctl", "--user", "is-active", service]).stdout.strip()
            if state != "active":
                raise RuntimeError(f"{service} não está ativo")
        validar_prompt()
        statuses = validar_eventos()

        grupo_id = str(CONFIG.get("grupo_id") or "")
        arquivos = [
            (CONFIG.get("pdf"), CONFIG.get("legenda_pdf") or ""),
            (CONFIG.get("foto"), CONFIG.get("legenda_foto") or ""),
        ]
        if any(path for path, _ in arquivos) and not grupo_id:
            raise RuntimeError("grupo_id é obrigatório para testar arquivos reais")
        for path, legenda in arquivos:
            if not path:
                continue
            evidencias.append(validar_arquivo(str(path), str(legenda), grupo_id))
            if CONFIG.get("testar_agente"):
                evidencias_agente.append(
                    validar_agente(str(path), str(legenda), grupo_id)
                )

        after = snapshot()
        if before != after:
            raise RuntimeError("assinaturas do Supabase mudaram durante os testes")
        print(
            json.dumps(
                {
                    "ok": True,
                    "py_compile": True,
                    "testes_handlers": True,
                    "openclaw_config": True,
                    "pre_processamento_anthropic": True,
                    "gateway": "active",
                    "trabalhador_ocr": "active",
                    "status_eventos": statuses,
                    "arquivos_reais": evidencias,
                    "agente_simulado": bool(
                        CONFIG.get("testar_agente") and evidencias
                    ),
                    "trajetorias_agente": evidencias_agente,
                    "supabase_inalterado": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        limpar_temporarios(cache_antes)


if __name__ == "__main__":
    raise SystemExit(main())
