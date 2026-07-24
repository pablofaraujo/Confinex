#!/usr/bin/env python3
"""Roteia confirmacoes textuais de promocao operacional vindas do Telegram."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
from typing import Any
HERE = Path(__file__).resolve().parent


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except PermissionError:
        return False


for path in (HERE, Path('/root/ponte/tools')):
    path_text = str(path)
    if path_exists(path) and path_text not in sys.path:
        sys.path.insert(0 if path == HERE else len(sys.path), path_text)
from confinex_client import ConfinexClient, ConfinexError
from promocao_operacional import execute_promotion, expected_confirmation

PROMOTE_RE=re.compile(r'^\s*PROMOVER\s+([0-9a-fA-F-]{8,})\s*$', re.I)


def parse_promote(text: str) -> str | None:
    m=PROMOTE_RE.match(text or '')
    return m.group(1) if m else None


def route_confirmation(client: Any, *, texto: str, grupo_id: str, mensagem_id: str | None, usuario: str, executar: bool=True) -> dict[str, Any]:
    action_id=parse_promote(texto)
    if not action_id:
        return {'ok': True, 'handled': False, 'motivo': 'mensagem_nao_e_promocao'}
    if not mensagem_id:
        raise ConfinexError('mensagem_id e obrigatorio para confirmar promocao operacional')
    result=execute_promotion(
        client,
        action_id,
        usuario=usuario,
        executar=executar,
        confirmacao=expected_confirmation(action_id),
        origem_conversa_id=grupo_id,
        origem_mensagem_id=mensagem_id,
    )
    return {'ok': True, 'handled': True, 'pending_action_id': action_id, 'resultado': result}


def build_parser():
    p=argparse.ArgumentParser(description='Roteia mensagem PROMOVER <id> para o executor operacional controlado')
    p.add_argument('--texto', required=True)
    p.add_argument('--grupo-id', required=True)
    p.add_argument('--mensagem-id')
    p.add_argument('--usuario', default='pablo')
    p.add_argument('--preview', action='store_true', help='Valida a pendencia sem executar gravacao operacional')
    return p


def main() -> int:
    args=build_parser().parse_args()
    try:
        out=route_confirmation(ConfinexClient(), texto=args.texto, grupo_id=args.grupo_id, mensagem_id=args.mensagem_id, usuario=args.usuario, executar=not args.preview)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0
    except ConfinexError as exc:
        print(json.dumps({'erro': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
