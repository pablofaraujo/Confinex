#!/usr/bin/env python3
"""Prepara patch mínimo/reversível do Telegram instalado; não grava arquivos."""

import argparse
import hashlib
import json
from pathlib import Path

IMPORTACAO = 'import { enriquecerContextoJuan } from "./continuidade_juan.mjs"; // confinex-continuidade-v1\n'
ANTES = 'untrustedContext: promptContext.length > 0 ? promptContext : void 0'
DEPOIS = ('untrustedContext: await enriquecerContextoJuan('
          '{ agentId: route.agentId, sessionKey: route.sessionKey, text: rawBody }, promptContext)')


def preparar(fonte, importacao=IMPORTACAO):
    if importacao in fonte and fonte.count(DEPOIS) == 1 and ANTES not in fonte:
        return fonte
    if 'confinex-continuidade-v1' in fonte or fonte.count(ANTES) != 1:
        raise ValueError('runtime divergente: revisão necessária, nada deve ser sobrescrito')
    # As variáveis precisam estar no construtor autenticado, não em texto do usuário.
    posicao = fonte.index(ANTES)
    trecho = fonte[max(0, posicao-8000):posicao]
    if not all(x in trecho for x in ('route.agentId', 'route.sessionKey', 'rawBody', 'const ctxPayload = await')):
        raise ValueError('ponto de entrada desconhecido')
    return importacao + fonte.replace(ANTES, DEPOIS, 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime', type=Path)
    p.add_argument('--adaptador', type=Path, help='Local privado de implantação do adaptador; não fica no repositório')
    p.add_argument('--conteudo', action='store_true', help='Emite somente o código proposto; não aplica')
    args = p.parse_args()
    original = args.runtime.read_text()
    importacao = (f'import {{ enriquecerContextoJuan }} from {json.dumps(str(args.adaptador.resolve()))}; '
                  '// confinex-continuidade-v1\n') if args.adaptador else IMPORTACAO
    proposta = preparar(original, importacao)
    if args.conteudo:
        print(proposta, end='')
    else:
        print(json.dumps({'alteracao_necessaria': original != proposta, 'escritas': 0,
                          'sha256_antes': hashlib.sha256(original.encode()).hexdigest(),
                          'sha256_depois': hashlib.sha256(proposta.encode()).hexdigest()}))


if __name__ == '__main__':
    main()
