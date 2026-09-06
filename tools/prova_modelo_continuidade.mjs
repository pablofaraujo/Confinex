// Prova de decisão do modelo, sem executor genérico ou entrega ao Telegram.
import { createHash } from 'node:crypto';
import { lstatSync, readFileSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';
import { spawnSync } from 'node:child_process';

const CHAVE = /^agent:juan:telegram:group:-?\d+(?::topic:\d+)?$/;
export class ProvaRecusada extends Error {}
const recusar = motivo => { throw new ProvaRecusada(motivo); };
const copiar = valor => JSON.parse(JSON.stringify(valor));
const chavesExatas = (valor, chaves) => valor && typeof valor === 'object'
  && !Array.isArray(valor) && Object.keys(valor).sort().join(',') === [...chaves].sort().join(',');

export function comandoConsulta(script, chave) {
  if (!isAbsolute(script) || !/^[\w/.-]+$/.test(script) || !CHAVE.test(chave ?? '')) {
    recusar('identidade_ou_caminho_invalido');
  }
  return `printf '%s' '${JSON.stringify({ chave_sessao: chave })}' | /usr/bin/python3 ${script} --entrada-stdin`;
}

// Os hashes são fornecidos pelo operador após revisar os arquivos, nunca pelo
// modelo. O modelo não escolhe executável, caminho, grupo, ambiente ou argumento.
export function criarConsultaProtegida({ script, chave, hashes, executar = spawnSync }) {
  const comando = comandoConsulta(script, chave);
  let fonte;
  try { fonte = readFileSync(script, 'utf8'); } catch { recusar('arquivo_indisponivel'); }
  const pontes = [...fonte.matchAll(/^PONTE = Path\('([^']+)'\)$/gm)];
  if (pontes.length !== 1 || !isAbsolute(pontes[0][1])) recusar('ponte_nao_identificada');
  const arquivos = [script, join(dirname(script), 'recuperar_contexto_juan.py'), pontes[0][1]];
  let usada = false;
  function conferir() {
    for (const arquivo of arquivos) {
      if (!/^[a-f0-9]{64}$/.test(hashes?.[arquivo] ?? '')) recusar('hash_nao_aprovado');
      try {
        if (!lstatSync(arquivo).isFile()) recusar('arquivo_nao_regular');
        if (createHash('sha256').update(readFileSync(arquivo)).digest('hex') !== hashes[arquivo]) {
          recusar('arquivo_alterado');
        }
      } catch (erro) {
        if (erro instanceof ProvaRecusada) throw erro;
        recusar('arquivo_indisponivel');
      }
    }
  }
  conferir();
  return {
    comando,
    validar(chamada) {
      if (usada) recusar('consulta_ja_utilizada');
      const campo = chamada?.name === 'exec' ? 'command'
        : chamada?.name === 'exec_command' ? 'cmd' : null;
      if (!campo || !chavesExatas(chamada.arguments, [campo])
          || chamada.arguments[campo] !== comando) recusar('capacidade_nao_permitida');
      conferir();
    },
    consultar(chamada) {
      this.validar(chamada);
      usada = true; // Inclui timeout/falha: não repetir nem recorrer a outro cliente.
      let processo;
      try {
        processo = executar('/usr/bin/python3', [script, '--entrada-stdin'], {
          input: JSON.stringify({ chave_sessao: chave }), encoding: 'utf8', shell: false,
          env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', PYTHONDONTWRITEBYTECODE: '1' },
          timeout: 50_000, maxBuffer: 150_000,
        });
      } catch { recusar('leitor_indisponivel'); }
      if (processo.error || processo.status !== 0) recusar('leitor_indisponivel');
      let resultado;
      try { resultado = JSON.parse(processo.stdout); } catch { recusar('leitor_resposta_invalida'); }
      if (resultado?.escritas !== 0 || resultado?.autoriza_escrita !== false
          || !Array.isArray(resultado.candidatos) || !Array.isArray(resultado.consultas)
          || !resultado.cobertura || typeof resultado.cobertura.parcial !== 'boolean') {
        recusar('leitor_contrato_invalido');
      }
      if (!['operation_drafts', 'pending_actions'].every(tabela =>
        resultado.consultas.some(c => c.tabela === tabela && c.status === 'ok'))
          || resultado.consultas.some(c => c.status !== 'ok')) {
        recusar('consulta_nao_confirmada');
      }
      return resultado;
    },
  };
}

// inferir deve ser transporte de COMPLETION, não um loop de agente. Somente
// devolve AssistantMessage (texto/toolCall); nenhum executor é passado a ele.
export async function executarProvaModelo({ contexto, inferir, consulta, timeoutMs = 90_000 }) {
  if (!contexto || !Array.isArray(contexto.messages) || !Array.isArray(contexto.tools)
      || !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120_000) {
    recusar('prova_configuracao_invalida');
  }
  const mensagens = copiar(contexto.messages);
  const chamadas = [];
  let resultadoConsulta;
  const ids = new Set();
  for (let turno = 0; turno < 2; turno += 1) {
    const abortar = new AbortController();
    let timer;
    let resposta;
    try {
      resposta = await Promise.race([
        Promise.resolve().then(() => inferir({ ...copiar(contexto), messages: copiar(mensagens) },
          { signal: abortar.signal, maxTokens: 2200 })),
        new Promise((_, reject) => { timer = setTimeout(() => {
          abortar.abort(); reject(new ProvaRecusada('modelo_limite_de_tempo'));
        }, timeoutMs); }),
      ]);
    } catch (erro) {
      if (erro instanceof ProvaRecusada) throw erro;
      recusar('modelo_indisponivel'); // Nunca propagar mensagem contendo token/payload.
    } finally { clearTimeout(timer); }
    if (resposta?.role !== 'assistant' || !Array.isArray(resposta.content)
        || !['stop', 'toolUse'].includes(resposta.stopReason)) recusar('modelo_resposta_incompleta');
    const requisicoes = resposta.content.filter(item => item.type === 'toolCall');
    if (requisicoes.length) {
      // Validar o lote inteiro antes de executar até mesmo a consulta permitida.
      if (requisicoes.length !== 1 || chamadas.length) recusar('excesso_de_ferramentas');
      const chamada = requisicoes[0];
      if (typeof chamada.id !== 'string' || !chamada.id || ids.has(chamada.id)) recusar('chamada_invalida');
      consulta.validar(chamada);
      resultadoConsulta = consulta.consultar(chamada);
      ids.add(chamada.id);
      chamadas.push({ ferramenta: chamada.name, resultado: 'consulta_somente_leitura' });
      mensagens.push(copiar(resposta), { role: 'toolResult', toolCallId: chamada.id,
        toolName: chamada.name, content: [{ type: 'text', text: JSON.stringify(resultadoConsulta) }],
        isError: false, timestamp: Date.now() });
      continue;
    }
    if (chamadas.length !== 1) recusar('modelo_nao_consultou');
    const texto = resposta.content.filter(item => item.type === 'text').map(item => item.text).join('\n').trim();
    if (!texto) recusar('modelo_sem_resposta');
    return { classificacao: 'CONSULTA_EXECUTADA_RESPOSTA_A_CONFERIR',
      chamadas, resposta: texto, consulta: resultadoConsulta, escritas: 0, entregas: 0,
      limite_da_prova: 'Decisão do modelo em ambiente restrito; não é envio pelo Telegram nem teste de gravação.' };
  }
  recusar('modelo_nao_concluiu');
}
