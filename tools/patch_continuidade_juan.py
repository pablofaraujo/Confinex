#!/usr/bin/env python3
"""Prepara patch mínimo/reversível do Telegram instalado; não grava arquivos."""

import argparse
import hashlib
import json
from pathlib import Path

MARCADOR_V1 = '// confinex-continuidade-v1'
MARCADOR_V2 = '// confinex-continuidade-v2'
IMPORTACAO = f'import {{ enriquecerContextoJuan }} from "./continuidade_juan.mjs"; {MARCADOR_V2}\n'
ANTES = 'untrustedContext: promptContext.length > 0 ? promptContext : void 0'
DEPOIS_V1 = ('untrustedContext: await enriquecerContextoJuan('
             '{ agentId: route.agentId, sessionKey: route.sessionKey, text: rawBody }, promptContext)')
DEPOIS = ('untrustedContext: await enriquecerContextoJuan('
          '{ agentId: route.agentId, sessionKey: route.sessionKey, text: rawBody, telegram: {'
          ' senderId, chatId: String(chatId), kind: conversationKind,'
          ' threadId: threadSpec.id != null ? String(threadSpec.id) : null,'
          ' accountId: route.accountId, routeSessionKey: route.sessionKey,'
          ' mainSessionKey: route.mainSessionKey } }, promptContext)')


def _validar_variaveis_autenticadas(fonte, posicao):
    trecho = fonte[max(0, posicao-8000):posicao]
    obrigatorias = (
        'route.agentId', 'route.sessionKey', 'route.accountId', 'route.mainSessionKey',
        'rawBody', 'senderId', 'chatId', 'conversationKind', 'threadSpec.id',
        'const ctxPayload = await',
    )
    if not all(item in trecho for item in obrigatorias):
        raise ValueError('ponto de entrada desconhecido')


def preparar(fonte, importacao=IMPORTACAO):
    if MARCADOR_V2 in fonte:
        if (
            fonte.count(MARCADOR_V2) == 1 and MARCADOR_V1 not in fonte
            and fonte.count(importacao) == 1 and fonte.count(DEPOIS) == 1
            and ANTES not in fonte and DEPOIS_V1 not in fonte
        ):
            return fonte
        raise ValueError('runtime divergente: revisão necessária, nada deve ser sobrescrito')
    if MARCADOR_V1 in fonte:
        importacao_v1 = importacao.replace(MARCADOR_V2, MARCADOR_V1)
        if (
            fonte.count(MARCADOR_V1) != 1 or fonte.count(importacao_v1) != 1
            or fonte.count(DEPOIS_V1) != 1 or DEPOIS in fonte or ANTES in fonte
        ):
            raise ValueError('runtime divergente: revisão necessária, nada deve ser sobrescrito')
        posicao = fonte.index(DEPOIS_V1)
        _validar_variaveis_autenticadas(fonte, posicao)
        return fonte.replace(MARCADOR_V1, MARCADOR_V2, 1).replace(DEPOIS_V1, DEPOIS, 1)
    if (
        'enriquecerContextoJuan' in fonte or fonte.count(ANTES) != 1
        or DEPOIS_V1 in fonte or DEPOIS in fonte
    ):
        raise ValueError('runtime divergente: revisão necessária, nada deve ser sobrescrito')
    # As variáveis precisam estar no construtor autenticado, não em texto do usuário.
    posicao = fonte.index(ANTES)
    _validar_variaveis_autenticadas(fonte, posicao)
    return importacao + fonte.replace(ANTES, DEPOIS, 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime', type=Path)
    p.add_argument('--adaptador', type=Path, help='Local privado de implantação do adaptador; não fica no repositório')
    p.add_argument('--sha256-esperado', required=True, help='SHA-256 conferido do runtime antes do patch')
    p.add_argument('--conteudo', action='store_true', help='Emite somente o código proposto; não aplica')
    args = p.parse_args()
    original_bytes = args.runtime.read_bytes()
    sha256_antes = hashlib.sha256(original_bytes).hexdigest()
    if args.sha256_esperado != sha256_antes:
        raise SystemExit('runtime divergente: SHA-256 não confere; nenhuma proposta emitida')
    try:
        original = original_bytes.decode('utf-8')
    except UnicodeDecodeError:
        raise SystemExit('runtime divergente: conteúdo não é UTF-8; nenhuma proposta emitida') from None
    importacao = (f'import {{ enriquecerContextoJuan }} from {json.dumps(str(args.adaptador.resolve()))}; '
                  f'{MARCADOR_V2}\n') if args.adaptador else IMPORTACAO
    proposta = preparar(original, importacao)
    if args.conteudo:
        print(proposta, end='')
    else:
        print(json.dumps({'alteracao_necessaria': original != proposta, 'escritas': 0,
                          'sha256_antes': sha256_antes,
                          'sha256_depois': hashlib.sha256(proposta.encode()).hexdigest(),
                          'config_privada': '/etc/confinex/recuperacao-mesa-juan.json'}))


if __name__ == '__main__':
    main()
