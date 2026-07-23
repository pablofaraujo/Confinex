#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'revisoes.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(match => match[1]).filter(Boolean);
assert.equal(scripts.length, 1, 'revisoes.html deve ter um script inline');

const context = {
  CFAgro: {authInit() {}},
  document: {getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; }},
  esc(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])); },
  fmtDT(value) { return String(value ?? ''); },
  console,
};
vm.createContext(context);
new vm.Script(`${scripts[0]}\nglobalThis.__revisoes={buildPromocaoPreview,promotionValidationState,promotionInputElement,aplicarEstadoPromocao,businessFieldIndex,businessTargetPath,promotionMissingLinks,irParaCampoObrigatorio,validarNegocioOperacional,planoDecisao,montarEventoDecisao,montarAtualizacaoRascunho,registrarEvento,promotionHistoryData,promotionHistoryHtml,statusPrincipal,dadosItem,labelStatus,painelFila,itemMatchesStatus,camposObrigatoriosFaltantes,filtrosRapidosHtml,contextosResumoHtml,contextoDe,grupoNome};`, {filename: 'revisoes.html'}).runInContext(context);

const api = context.__revisoes;

const compraIncompleta = api.buildPromocaoPreview({}, 'compras');
const estadoCompraIncompleta = api.promotionValidationState('compras', compraIncompleta);
assert.equal(estadoCompraIncompleta.blocked, true);
assert.deepEqual([...estadoCompraIncompleta.labels], ['Negócio selecionado', 'Data', 'Cabeças', 'Valor total']);
assert.match(estadoCompraIncompleta.aviso, /Negócio selecionado, Data, Cabeças, Valor total/);

const compraCompleta = api.buildPromocaoPreview({operacao_id:'op-1',data_compra:'2026-07-22',quantidade:18,valor_total:115033.27}, 'compras');
assert.equal(api.promotionValidationState('compras', compraCompleta).blocked, false);

const vendaSemRecebimento = api.buildPromocaoPreview({data_abate:'2026-07-22',cabecas:18,peso_liquido_kg:5228.785,valor_bruto:115033.27}, 'vendas');
const estadoVenda = api.promotionValidationState('vendas', vendaSemRecebimento);
assert.equal(estadoVenda.blocked, true);
assert.deepEqual([...estadoVenda.labels], ['Previsão de recebimento']);
const vendaCompleta = api.buildPromocaoPreview({data_abate:'2026-07-22',cabecas:18,peso_carcaca_total:5228.785,valor_bruto:115033.27,prazo_recebimento:'2026-08-21'}, 'vendas');
assert.equal(api.promotionValidationState('vendas', vendaCompleta).blocked, false);

const pesagemIncompleta = api.buildPromocaoPreview({}, 'pesagens_caderno');
assert.deepEqual([...api.promotionValidationState('pesagens_caderno', pesagemIncompleta).labels], ['Contexto', 'Data da folha', 'Peso kg']);
const pesagemCompleta = api.buildPromocaoPreview({contexto_operacional:'Confinamento',data_folha:'2026-07-22',peso_kg:5228.785}, 'pesagens_caderno');
assert.equal(api.promotionValidationState('pesagens_caderno', pesagemCompleta).blocked, false);

const abateIncompleto = api.buildPromocaoPreview({}, 'abates');
assert.deepEqual([...api.promotionValidationState('abates', abateIncompleto).labels], ['Data do abate', 'Lote', 'Cabeças', 'Peso líquido kg']);
const abateCompleto = api.buildPromocaoPreview({data_abate:'2026-07-22',lote:'L-5',cabecas:18,peso_liquido_kg:5228.785}, 'abates');
assert.equal(api.promotionValidationState('abates', abateCompleto).blocked, false);

assert.equal(api.businessTargetPath('vendas', 'data_compra|data_abate|data_folha|data', {}), 'data_abate');
assert.equal(api.businessTargetPath('pesagens_caderno', 'peso_total_kg|peso_liquido_kg|peso_kg', {}), 'peso_kg');
assert.equal(api.businessTargetPath('abates', 'peso_total_kg|peso_liquido_kg|peso_kg', {}), 'peso_liquido_kg');
assert.doesNotThrow(() => api.validarNegocioOperacional({}, 'vendas'));
assert.doesNotThrow(() => api.validarNegocioOperacional({}, 'pesagens_caderno'));
assert.doesNotThrow(() => api.validarNegocioOperacional({}, 'abates'));
assert.throws(() => api.validarNegocioOperacional({}, 'compras'), /Selecione um negócio existente/);

class Classes {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
}
const containers = [];
const fields = new Map();
function makeField(selector) {
  const container = {classList:new Classes()};
  const attributes = new Map();
  const field = {
    focused:false,
    scrolled:false,
    closest() { return container; },
    setAttribute(name, value) { attributes.set(name, value); },
    removeAttribute(name) { attributes.delete(name); },
    getAttribute(name) { return attributes.get(name); },
    focus() { this.focused = true; },
    scrollIntoView() { this.scrolled = true; },
  };
  fields.set(selector, field);
  containers.push(container);
}
const requiredSelectors = new Set(['#negocioSelect','#ctx']);
for (const field of ['data_compra','quantidade','valor_total','data_abate','cabecas','peso_carcaca_total','valor_bruto','prazo_recebimento','data_folha','peso_kg','lote','peso_liquido_kg']) {
  requiredSelectors.add(`[data-biz="${api.businessFieldIndex(field)}"]`);
}
requiredSelectors.forEach(makeField);
const alerta = {hidden:true,innerHTML:''};
const preparar = {disabled:false,title:''};
const salvar = {disabled:false};
context.document.getElementById = id => ({promotionAlert:alerta,btnPrepararPromocao:preparar,btnSalvarAjustes:salvar})[id] || null;
context.document.querySelector = selector => fields.get(selector) || null;
context.document.querySelectorAll = selector => {
  if (selector === '.campo-incompleto') return containers.filter(node => node.classList.contains('campo-incompleto'));
  if (selector === '[aria-invalid="true"]') return [...fields.values()].filter(node => node.getAttribute('aria-invalid') === 'true');
  return [];
};

const simulacoesVisuais = [
  ['compras', compraIncompleta, compraCompleta, 4, ['Negócio selecionado','Data','Cabeças','Valor total']],
  ['vendas', vendaSemRecebimento, vendaCompleta, 1, ['Previsão de recebimento']],
  ['pesagens_caderno', pesagemIncompleta, pesagemCompleta, 3, ['Contexto','Data da folha','Peso kg']],
  ['abates', abateIncompleto, abateCompleto, 4, ['Data do abate','Lote','Cabeças','Peso líquido kg']],
];
for (const [target,incompleto,completo,totalFaltante,labels] of simulacoesVisuais) {
  api.aplicarEstadoPromocao(target, incompleto);
  assert.equal(alerta.hidden, false);
  assert.match(alerta.innerHTML, /Complete os campos indicados/);
  for (const label of labels) assert.match(alerta.innerHTML, new RegExp(label));
  assert.equal(containers.filter(node => node.classList.contains('campo-incompleto')).length, totalFaltante);
  assert.equal([...fields.values()].filter(node => node.getAttribute('aria-invalid') === 'true').length, totalFaltante);
  assert.equal(preparar.disabled, true);
  assert.equal(salvar.disabled, false, 'Salvar ajustes deve continuar permitido');

  api.aplicarEstadoPromocao(target, completo);
  assert.equal(alerta.hidden, true);
  assert.equal(preparar.disabled, false);
  assert.equal(containers.filter(node => node.classList.contains('campo-incompleto')).length, 0);
  assert.equal([...fields.values()].filter(node => node.getAttribute('aria-invalid') === 'true').length, 0);
}

const campoRecebimento = api.promotionInputElement('vendas', 'prazo_recebimento');
assert.equal(api.irParaCampoObrigatorio('vendas', 'prazo_recebimento'), true);
assert.equal(campoRecebimento.focused, true);
assert.equal(campoRecebimento.scrolled, true);
assert.match(api.promotionMissingLinks('vendas', ['prazo_recebimento']), /data-key="prazo_recebimento"/);

assert.match(html, /id="btnSalvarAjustes"[^>]*onclick="salvarAjustes\('em_revisao'\)"/);
assert.doesNotMatch(html.match(/<button[^>]*id="btnSalvarAjustes"[^>]*>/)?.[0] || '', /disabled/);

const contextoCompleto = (grupo,mensagem) => ({
  contexto_operacional:grupo,
  grupo_telegram:grupo,
  origem_canal:'telegram',
  origem_mensagem_id:mensagem,
  agente:'juan',
  status_confirmacao:'pendente',
});
const promocaoSimulada = (status,grupo,id) => ({
  id:`pa-${id}`,
  draft:null,
  action:{
    id:`a-${id}`,
    acao_tipo:'promover_revisao_operacional',
    status,
    entidade_tipo:'compras',
    payload:{
      target_table:'compras',
      dados_revisados:contextoCompleto(grupo,`msg-${id}`),
      proposed_record:{operacao_id:'op-1',data:'2026-07-22',quantidade:18,valor_total:115033.27},
    },
  },
});
const itensPainel = [
  {id:'d-incompleto',draft:{id:'d-incompleto',status:'em_revisao',tipo_operacao:'compra',dados_extraidos:contextoCompleto('Boi Balança','msg-1')},action:null},
  {id:'d-completo',draft:{id:'d-completo',status:'em_revisao',tipo_operacao:'compra',dados_extraidos:{...contextoCompleto('Confinamento','msg-2'),operacao_id:'op-1',data_compra:'2026-07-22',quantidade:18,valor_total:115033.27}},action:null},
  promocaoSimulada('aguardando_confirmacao','Boi Balança','aguarda'),
  promocaoSimulada('em_execucao','Confinamento','executa'),
  promocaoSimulada('executado','Boi Balança','executado'),
  promocaoSimulada('erro_pos_gravacao','Confinamento','erro'),
  {id:'d-cancelado',draft:{id:'d-cancelado',status:'cancelado',tipo_operacao:'compra',dados_extraidos:contextoCompleto('telegram:-9999999999','msg-7')},action:null},
  {id:'pa-rejeitada',draft:null,action:{id:'a-rejeitada',status:'rejeitado',acao_tipo:'revisar_compra',payload:{dados_revisados:contextoCompleto('Ceci e Juan','msg-8')}}},
];
const painel = api.painelFila(itensPainel);
assert.equal(painel.aguardandoRevisao, 2);
assert.equal(painel.camposFaltantes, 1);
assert.equal(painel.promocoesAguardando, 1);
assert.equal(painel.promocoesExecutando, 1);
assert.equal(painel.promocoesExecutadas, 1);
assert.equal(painel.errosPosGravacao, 1);
assert.equal(painel.rejeitadosCancelados, 2);
assert.equal(api.camposObrigatoriosFaltantes(itensPainel[0]).length, 4);
assert.equal(api.camposObrigatoriosFaltantes(itensPainel[1]).length, 0);
assert.equal(itensPainel.filter(item => api.itemMatchesStatus(item,'promocao_aguardando')).length, 1);
assert.equal(itensPainel.filter(item => api.itemMatchesStatus(item,'campos_faltantes')).length, 1);
assert.equal(itensPainel.filter(item => api.itemMatchesStatus(item,'rejeitados_cancelados')).length, 2);
assert.equal(api.grupoNome('telegram:-9999999999'), 'Contexto não identificado');
assert.doesNotMatch(JSON.stringify(painel.contextos), /9999999999/);
assert.match(api.filtrosRapidosHtml(painel), /data-filter="campos_faltantes"/);
assert.match(api.contextosResumoHtml(painel), /Boi Balança/);
assert.doesNotMatch(api.contextosResumoHtml(painel), /9999999999|telegram:-/);

assert.throws(() => api.planoDecisao('rejeitado', '   '), /Informe o motivo/);
const rejeicao = api.planoDecisao('rejeitado', 'Documento pertence a outro lote');
assert.equal(rejeicao.draftStatus, 'cancelado');
assert.equal(rejeicao.actionStatus, 'rejeitado');
assert.equal(rejeicao.eventoStatus, 'rejeitada');
const itemDecisao = {draft:{id:'draft-1',agente:'juan',codigo_sugerido:'CF-1'},action:{id:'action-1'}};
const dadosDecisao = {contexto_operacional:'Confinamento',quantidade:18,origem_canal:'telegram',origem_conversa_id:'grupo-1',origem_mensagem_id:'msg-1'};
const eventoRejeicao = api.montarEventoDecisao(rejeicao, itemDecisao, dadosDecisao);
assert.equal(eventoRejeicao.tipo, 'revisao_rejeitada');
assert.equal(eventoRejeicao.status, 'registrado');
assert.equal(eventoRejeicao.dados.status_decisao, 'rejeitada');
assert.equal(eventoRejeicao.dados.motivo, 'Documento pertence a outro lote');
assert.match(eventoRejeicao.observacao, /Documento pertence a outro lote/);

const devolucao = api.planoDecisao('aguardando_confirmacao', 'Quantidade corrigida');
assert.equal(devolucao.draftStatus, 'aguardando_confirmacao');
assert.equal(devolucao.actionStatus, 'aguardando_confirmacao');
const atualizacaoDevolucao = api.montarAtualizacaoRascunho(devolucao,dadosDecisao,{origem:'telegram'},['confirmar quantidade'],'2026-07-22T12:00:00Z',itemDecisao.draft,itemDecisao.action);
assert.equal(atualizacaoDevolucao.dados_extraidos, dadosDecisao, 'Voltar para confirmação deve manter os dados ajustados');
assert.equal(atualizacaoDevolucao.status, 'aguardando_confirmacao');
const eventoDevolucao = api.montarEventoDecisao(devolucao,itemDecisao,dadosDecisao);
assert.equal(eventoDevolucao.tipo, 'revisao_devolvida_para_confirmacao');
assert.equal(eventoDevolucao.dados.motivo, 'Quantidade corrigida');

const ajustes = api.planoDecisao('em_revisao', 'Peso conferido');
assert.equal(ajustes.draftStatus, 'em_revisao');
assert.equal(ajustes.actionStatus, 'em_revisao');
const eventoAjustes = api.montarEventoDecisao(ajustes,itemDecisao,dadosDecisao);
assert.equal(eventoAjustes.tipo, 'ajustes_salvos_na_revisao');
assert.equal(eventoAjustes.status, 'registrado');
assert.equal(eventoAjustes.dados.status_decisao, 'ajustes_salvos');
assert.match(html, /onclick="voltarParaConfirmacao\(\)"/);
assert.match(html, /Obrigatório para rejeitar/);
assert.doesNotMatch(html, /Dados técnicos avançados|class="json"/);

const basePromocao = {
  acao_tipo:'promover_revisao_operacional',
  criado_em:'2026-07-22T10:00:00Z',
  atualizado_em:'2026-07-22T10:05:00Z',
  agente:'juan',
  entidade_tipo:'compras',
  payload:{target_table:'compras',dados_revisados:{contexto_operacional:'Boi Balança'}},
};
const estados = [
  [{...basePromocao,status:'aguardando_confirmacao'}, 'Aguardando confirmação', 'Esperando sua confirmação'],
  [{...basePromocao,status:'em_execucao',confirmado_em:'2026-07-22T10:06:00Z',confirmado_por:'pablo'}, 'Em andamento', 'está sendo gravado'],
  [{...basePromocao,status:'executado',resultado:{target_record_id:'compra-123'}}, 'Concluído', 'foi concluído'],
  [{...basePromocao,status:'erro_pos_gravacao',resultado:{target_record_id:'compra-456',detalhe:{segredo:'não mostrar'}}}, 'Precisa conferir', 'histórico precisa ser conferido'],
  [{...basePromocao,status:'rejeitado',erro:'mensagem técnica que não deve aparecer'}, 'Rejeitado', 'foi rejeitada'],
];
for (const [action,statusLabel,descricao] of estados) {
  const history = api.promotionHistoryData(action);
  assert.equal(history.statusLabel, statusLabel);
  assert.match(history.descricao, new RegExp(descricao));
  const rendered = api.promotionHistoryHtml(action);
  assert.match(rendered, new RegExp(statusLabel));
  assert.doesNotMatch(rendered, /segredo|mensagem técnica|\{\s*"/);
}
const concluida = api.promotionHistoryData(estados[2][0]);
assert.equal(concluida.operationalId, 'compra-123');
assert.equal(concluida.agente, 'juan');
assert.equal(concluida.destino, 'Compra de gado');
assert.equal(api.statusPrincipal({draft:{status:'em_revisao'},action:estados[2][0]}), 'executado');
assert.equal(api.dadosItem({}, basePromocao).contexto_operacional, 'Boi Balança');
assert.doesNotMatch(html, /Dados técnicos avançados|class="json"/);
const eventosInseridos = [];
context.db = {from(table) { assert.equal(table, 'eventos'); return {insert(record) { eventosInseridos.push(record); return {error:null}; }}; }};
api.registrarEvento(rejeicao,itemDecisao,dadosDecisao).then(() => {
  assert.equal(eventosInseridos.length, 1, 'Rejeição com motivo deve registrar evento');
  assert.equal(eventosInseridos[0].status, 'registrado');
  assert.equal(eventosInseridos[0].dados.status_decisao, 'rejeitada');
  console.log('Simulações da fila de revisões: OK');
}).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
