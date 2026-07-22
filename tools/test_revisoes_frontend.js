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
new vm.Script(`${scripts[0]}\nglobalThis.__revisoes={buildPromocaoPreview,promotionValidationState,promotionInputElement,aplicarEstadoPromocao,businessFieldIndex,promotionHistoryData,promotionHistoryHtml,statusPrincipal,dadosItem,labelStatus};`, {filename: 'revisoes.html'}).runInContext(context);

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

assert.deepEqual([...api.promotionValidationState('pesagens_caderno', {}).labels], ['Contexto', 'Data da folha', 'Peso kg']);
assert.deepEqual([...api.promotionValidationState('abates', {}).labels], ['Data do abate', 'Lote', 'Cabeças', 'Peso líquido kg']);

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
  const field = {closest() { return container; }};
  fields.set(selector, field);
  containers.push(container);
}
makeField('#negocioSelect');
for (const field of ['data_compra','quantidade','valor_total']) makeField(`[data-biz="${api.businessFieldIndex(field)}"]`);
const alerta = {hidden:true,innerHTML:''};
const preparar = {disabled:false,title:''};
const salvar = {disabled:false};
context.document.getElementById = id => ({promotionAlert:alerta,btnPrepararPromocao:preparar,btnSalvarAjustes:salvar})[id] || null;
context.document.querySelector = selector => fields.get(selector) || null;
context.document.querySelectorAll = selector => selector === '.campo-incompleto' ? containers.filter(node => node.classList.contains('campo-incompleto')) : [];

api.aplicarEstadoPromocao('compras', compraIncompleta);
assert.equal(alerta.hidden, false);
assert.match(alerta.innerHTML, /Revise os campos destacados/);
assert.equal(containers.filter(node => node.classList.contains('campo-incompleto')).length, 4);
assert.equal(preparar.disabled, true);
assert.equal(salvar.disabled, false, 'Salvar ajustes deve continuar permitido');

api.aplicarEstadoPromocao('compras', compraCompleta);
assert.equal(alerta.hidden, true);
assert.equal(preparar.disabled, false);
assert.equal(containers.filter(node => node.classList.contains('campo-incompleto')).length, 0);

assert.match(html, /id="btnSalvarAjustes"[^>]*onclick="salvarAjustes\('em_revisao'\)"/);
assert.doesNotMatch(html.match(/<button[^>]*id="btnSalvarAjustes"[^>]*>/)?.[0] || '', /disabled/);

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
console.log('Simulações da fila de revisões: OK');
