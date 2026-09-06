#!/usr/bin/env python3
"""Prévia e confirmação de comissão. Sem rede, banco, arquivos ou executor.

Identidades devem vir do canal autenticado, nunca do modelo/histórico. A chave
HMAC pertence ao futuro mediador. Este módulo não instala nem ativa esse mediador.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
ESTADOS = {'rascunho', 'em_revisao', 'aguardando_confirmacao'}
ACOES = {'revisar_compra', 'revisar_documento', 'revisar_consolidacao_negocio'}
IDENTIDADE = {'canal', 'agente', 'grupo_id', 'autor_id', 'mensagem_id', 'topico_id', 'autor_bot', 'encaminhada'}


class ComplementoRecusado(ValueError):
    """Mensagem segura, sem dados do negócio."""


def recusar(mensagem):
    raise ComplementoRecusado(mensagem)


def canonico(objeto):
    return json.dumps(objeto, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def numero(valor, casas, maximo):
    if isinstance(valor, bool) or not isinstance(valor, (str, int, float, Decimal)):
        recusar('Informe um número válido.')
    if len(str(valor)) > 40 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', str(valor)):
        recusar('Informe um número decimal sem abreviação.')
    try:
        n = Decimal(str(valor))
        if not n.is_finite() or n <= 0 or n > Decimal(maximo):
            recusar('Valor fora do intervalo permitido.')
        if n != n.quantize(Decimal(10) ** -casas):
            recusar('Confira as casas decimais do valor.')
        return n
    except (InvalidOperation, ValueError):
        recusar('Informe um número válido.')


def validar_identidade(identidade):
    if not isinstance(identidade, dict) or set(identidade) != IDENTIDADE:
        recusar('A origem autenticada da mensagem está incompleta.')
    if (identidade['canal'] != 'telegram' or identidade['agente'] != 'juan'
            or identidade['topico_id'] is not None or identidade['autor_bot'] is not False
            or identidade['encaminhada'] is not False
            or not isinstance(identidade['grupo_id'], str)
            or not re.fullmatch(r'-[1-9][0-9]{0,19}', identidade['grupo_id'])
            or any(not isinstance(identidade[k], str) or not re.fullmatch(r'[1-9][0-9]{0,19}', identidade[k])
                   for k in ('autor_id', 'mensagem_id'))):
        recusar('A mensagem precisa ser atual, do responsável e do grupo correto.')


def selecionar_candidato(candidatos, escolha=None):
    """Escolha explícita pertence ao mediador; sem ela, não desempatar por nome."""
    if not isinstance(candidatos, list) or not candidatos or any(not isinstance(c, dict) for c in candidatos):
        recusar('Não foi possível conferir os candidatos.')
    ids = [c.get('id') for c in candidatos]
    if any(not isinstance(i, str) or not UUID.fullmatch(i) for i in ids) or len(ids) != len(set(ids)):
        recusar('Os candidatos precisam ser conferidos novamente.')
    if escolha is None and len(candidatos) != 1:
        recusar('Há mais de um rascunho: escolha qual negócio receberá a comissão.')
    alvo = escolha if escolha is not None else ids[0]
    if alvo not in ids:
        recusar('A escolha não corresponde aos candidatos apresentados.')
    return copy.deepcopy(candidatos[ids.index(alvo)])


def conferir_par(rascunho, pendencia, identidade):
    validar_identidade(identidade)
    for registro in (rascunho, pendencia):
        if not isinstance(registro, dict) or not UUID.fullmatch(str(registro.get('id', ''))):
            recusar('Selecione uma revisão válida.')
        if (registro.get('status') not in ESTADOS or not registro.get('atualizado_em')
                or registro.get('origem_canal') != 'telegram'
                or registro.get('origem_conversa_id') != identidade['grupo_id']
                or registro.get('contexto_canonico') != 'telegram:grupo:' + identidade['grupo_id']
                or registro.get('escopo') != 'grupo'):
            recusar('Recarregue a revisão; o estado ou contexto precisa ser conferido.')
    if (rascunho.get('pending_action_id') != pendencia['id']
            or pendencia.get('entidade_tipo') != 'operation_draft'
            or pendencia.get('entidade_id') != rascunho['id']
            or pendencia.get('acao_tipo') not in ACOES
            or pendencia.get('canal') not in (None, 'telegram')
            or pendencia.get('conversa_id') not in (None, identidade['grupo_id'])
            or rascunho.get('tipo_operacao') != 'compra'
            or rascunho.get('entidade_final_id') is not None
            or rascunho.get('revisao_tipo', 'pre_revisao') != 'pre_revisao'):
        recusar('O vínculo da revisão não permite este complemento.')
    dados, payload = rascunho.get('dados_extraidos'), pendencia.get('payload')
    if (not isinstance(dados, dict) or not isinstance(payload, dict)
            or payload.get('dados_extraidos') != dados
            or any(k in payload and payload[k] != rascunho['id'] for k in ('operation_draft_id', 'source_draft_id'))
            or dados.get('status_confirmacao') in ('promocao_preparada', 'aprovado_confinex')
            or any(k in payload for k in ('target_table', 'proposed_record'))):
        recusar('Os dados ou vínculos divergem; confira a revisão antes de ajustar.')
    return dados


def preparar_comissao(rascunho, pendencia, *, identidade, percentual, beneficiario, agora):
    dados = conferir_par(rascunho, pendencia, identidade)
    if not isinstance(beneficiario, str) or not re.fullmatch(r"[^\x00-\x1f<>@/\\]{2,120}", beneficiario.strip()):
        recusar('Informe o nome do beneficiário da comissão.')
    if not isinstance(agora, int) or isinstance(agora, bool) or agora < 1:
        recusar('Horário da prévia inválido.')
    base = numero(dados.get('valor_total'), 2, '999999999999')
    pct = numero(percentual, 4, '100')
    comissao = {'beneficiario': beneficiario.strip(), 'percentual': format(pct, '.4f'),
        'base_vendedor': format(base, '.2f'),
        'valor': format((base * pct / 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), '.2f')}
    anterior = dados.get('comissao')
    if 'comissao' in dados and not isinstance(anterior, dict):
        recusar('A comissão anterior precisa de conferência.')
    plano = {'versao': 1, 'acao': 'definir_comissao', 'rascunho': copy.deepcopy(rascunho),
        'pendencia': copy.deepcopy(pendencia), 'pedido': copy.deepcopy(identidade),
        'comissao': comissao, 'criado_em_epoch': agora, 'expira_em_epoch': agora + 900}
    plano['plano_id'] = hashlib.sha256(canonico(plano).encode()).hexdigest()
    return plano


def assinar_previa(plano, segredo):
    if not isinstance(segredo, bytes) or len(segredo) < 32:
        recusar('A proteção da prévia não está configurada.')
    try:
        reconstruido = preparar_comissao(plano['rascunho'], plano['pendencia'],
            identidade=plano['pedido'], percentual=plano['comissao']['percentual'],
            beneficiario=plano['comissao']['beneficiario'], agora=plano['criado_em_epoch'])
        if canonico(plano) != canonico(reconstruido):
            recusar('A prévia precisa ser gerada novamente.')
    except (KeyError, TypeError, ValueError):
        recusar('A prévia precisa ser gerada novamente.')
    return {'plano': copy.deepcopy(plano), 'assinatura': hmac.new(segredo, canonico(plano).encode(), hashlib.sha256).hexdigest()}


def frase_confirmacao(plano):
    return 'CONFIRMAR COMISSAO ' + plano['plano_id'][:12]


def confirmar_previa(envelope, *, segredo, identidade, texto, agora):
    validar_identidade(identidade)
    if not isinstance(envelope, dict) or set(envelope) != {'plano', 'assinatura'}:
        recusar('A prévia precisa ser gerada novamente.')
    plano = envelope['plano']
    esperado = assinar_previa(plano, segredo)['assinatura']
    if not isinstance(envelope['assinatura'], str) or not hmac.compare_digest(esperado, envelope['assinatura']):
        recusar('A prévia foi alterada; nada foi gravado.')
    pedido = plano['pedido']
    if (not isinstance(agora, int) or isinstance(agora, bool)
            or not plano['criado_em_epoch'] <= agora <= plano['expira_em_epoch']
            or any(identidade[k] != pedido[k] for k in ('canal', 'agente', 'grupo_id', 'autor_id', 'topico_id'))
            or identidade['mensagem_id'] in (pedido['mensagem_id'], plano['rascunho'].get('origem_mensagem_id'), plano['pendencia'].get('origem_mensagem_id'))
            or texto != frase_confirmacao(plano)):
        recusar('Confirme a prévia atual com uma nova mensagem no mesmo grupo.')
    # Apenas prepara contrato. Não chama RPC nem aceita um cliente genérico.
    return {'p_plano': copy.deepcopy(plano), 'p_confirmacao': {**copy.deepcopy(identidade),
        'texto': texto, 'plano_id': plano['plano_id'], 'confirmado_em_epoch': agora}}


def resumo_previa(plano):
    c = plano['comissao']
    def moeda(v):
        return f'{Decimal(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    d = plano['rascunho']
    dados = d['dados_extraidos']
    def pista(*valores):
        # Texto plano; nunca exibir UUID, IDs de grupo, links ou controles.
        for v in valores:
            if isinstance(v, (str, int)) and not isinstance(v, bool):
                v = str(v).strip()
                if (v and len(v) <= 120 and not UUID.fullmatch(v)
                        and not re.search(r'[\x00-\x1f<>@/\\_]|telegram:|https?:|^-[0-9]+$', v)):
                    return v
        return 'Não informado'
    data = pista(dados.get('data_compra'), dados.get('data'))
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', data):
        data = '/'.join(reversed(data.split('-')))
    anterior = dados.get('comissao')
    return ('Comissão para conferir\n'
        f"Negócio: {pista(d.get('codigo_sugerido'), dados.get('operacao_codigo'))}\n"
        f"Grupo: {pista(d.get('contexto_nome'))}\n"
        f"Fornecedor: {pista(dados.get('fornecedor'), dados.get('vendedor'), dados.get('contraparte'))}\n"
        f"Data: {data} · Cabeças: {pista(dados.get('quantidade'), dados.get('cabecas'))}\n"
        f"Categoria: {pista(dados.get('categoria'))}\n"
        f"Beneficiário: {c['beneficiario']}\nBase do vendedor: R$ {moeda(c['base_vendedor'])}\n"
        f"Comissão: {c['percentual'].rstrip('0').rstrip('.').replace('.', ',')}% — R$ {moeda(c['valor'])}\n"
        f"Total com comissão: R$ {moeda(Decimal(c['base_vendedor']) + Decimal(c['valor']))}\n"
        + ('A comissão anterior será substituída, não somada novamente.\n' if anterior is not None else '')
        + 'O valor do vendedor não muda. Nada foi salvo nesta prévia.\n'
        + frase_confirmacao(plano))
