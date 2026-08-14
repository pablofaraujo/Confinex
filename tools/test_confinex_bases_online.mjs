import assert from 'node:assert/strict';
import {
  apagarBaseOnline,
  baseDaLinhaOnline,
  listarBasesOnline,
  mesclarBasesConfinamento,
  normalizarBaseConfinamento,
  salvarBaseOnline
} from '../js/confinex-bases-online.mjs';

const antiga = { id: 'base-1', nome: 'Ribas', km: '120', atualizadoEm: '2026-08-10T10:00:00.000Z' };
const nova = { id: 'base-1', nome: 'Ribas', km: '135', atualizadoEm: '2026-08-11T10:00:00.000Z' };
assert.equal(normalizarBaseConfinamento(antiga).chave, 'base-1');
assert.equal(normalizarBaseConfinamento(antiga).dados.km, '120');
assert.throws(() => normalizarBaseConfinamento({ nome: 'Sem chave' }), /identificação/);
assert.throws(() => normalizarBaseConfinamento({ id: 'x' }), /nome/);
assert.deepEqual(mesclarBasesConfinamento([antiga], [nova]).map((base) => base.km), ['135']);
assert.equal(mesclarBasesConfinamento([{ id: 'a', nome: 'Zeta' }, { id: 'b', nome: 'Alfa' }], [])[0].nome, 'Alfa');
assert.equal(baseDaLinhaOnline({ chave: 'x', nome: 'Online', dados: { km: 40 }, atualizado_em: '2026-08-12T00:00:00Z' }).dados.id, 'x');

const chamadas = [];
const sessao = { auth: { getSession: async () => ({ data: { session: { user: { id: 'usuario-teste' } } }, error: null }) } };
const supabase = {
  ...sessao,
  from(tabela) {
    chamadas.push(['from', tabela]);
    return {
      select(campos) {
        chamadas.push(['select', campos]);
        return { order: async () => ({ data: [{ chave: 'base-1', nome: 'Ribas', dados: nova, atualizado_em: nova.atualizadoEm }], error: null }) };
      },
      delete() {
        chamadas.push(['delete']);
        return { eq: async (campo, valor) => { chamadas.push(['eq', campo, valor]); return { error: null }; } };
      }
    };
  },
  async rpc(funcao, payload) {
    chamadas.push(['rpc', funcao, payload]);
    return { data: [{ chave: payload.p_chave, nome: payload.p_nome, dados: payload.p_dados, atualizado_em: payload.p_atualizado_em }], error: null };
  }
};

const listadas = await listarBasesOnline({ supabase });
assert.equal(listadas.length, 1);
assert.equal(listadas[0].km, '135');
const salva = await salvarBaseOnline({ supabase, base: nova });
assert.equal(salva.id, 'base-1');
assert.equal(chamadas.find((c) => c[0] === 'rpc')[1], 'salvar_base_confinex');
assert.deepEqual(await apagarBaseOnline({ supabase, chave: 'base-1' }), { chave: 'base-1', apagada: true });
assert.ok(chamadas.some((c) => c[0] === 'eq' && c[1] === 'chave' && c[2] === 'base-1'));

const semSessao = { auth: { getSession: async () => ({ data: { session: null }, error: null }) } };
await assert.rejects(() => listarBasesOnline({ supabase: semSessao }), /Entre no ecossistema/);
await assert.rejects(() => salvarBaseOnline({ supabase: semSessao, base: nova }), /Entre no ecossistema/);
await assert.rejects(() => apagarBaseOnline({ supabase: semSessao, chave: 'base-1' }), /Entre no ecossistema/);

const falha = {
  ...sessao,
  from: () => ({ select: () => ({ order: async () => ({ data: null, error: { message: 'indisponível' } }) }) }),
  rpc: async () => ({ data: null, error: { message: 'indisponível' } })
};
await assert.rejects(() => listarBasesOnline({ supabase: falha }), /indisponível/);
await assert.rejects(() => salvarBaseOnline({ supabase: falha, base: nova }), /indisponível/);

console.log('Bases online do Confinex: 18 verificações aprovadas');
