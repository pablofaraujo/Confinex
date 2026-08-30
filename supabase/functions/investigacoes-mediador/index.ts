import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";

const MAX_CORPO_BYTES = 300 * 1024;
const MAX_CONSULTAS = 50;
const MAX_CAMPOS_PENDENTES = 100;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TEXTO_CHAVE = /^[A-Za-z0-9._:-]{1,96}$/;
const CHAVE_JSON = /^[A-Za-z0-9_. -]{1,80}$/;

const CORS_HEADERS = {
  "access-control-allow-headers": "authorization, apikey, content-type, x-client-info",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-origin": "*",
  "content-type": "application/json; charset=utf-8",
  "vary": "Origin",
};

type Json = null | boolean | number | string | Json[] | { [chave: string]: Json };
type Acao = "consultar_bloqueios" | "preparar_promocao" | "decidir_corretiva";

class ErroPublico extends Error {
  constructor(readonly status: number, mensagem: string) {
    super(mensagem);
  }
}

function resposta(corpo: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(corpo), { status, headers: CORS_HEADERS });
}

function erro(status: number, mensagem: string): Response {
  return resposta({ erro: mensagem }, status);
}

function objeto(valor: unknown): valor is Record<string, Json> {
  return Boolean(valor) && typeof valor === "object" && !Array.isArray(valor);
}

function mesmasChaves(valor: Record<string, Json>, chaves: readonly string[]): boolean {
  const recebidas = Object.keys(valor);
  return recebidas.length === chaves.length && recebidas.every((chave) => chaves.includes(chave));
}

function texto(valor: unknown, maximo: number, permitirNulo = false): valor is string | null {
  return (permitirNulo && valor === null) ||
    (typeof valor === "string" && valor.length <= maximo);
}

function uuid(valor: unknown): valor is string {
  return typeof valor === "string" && UUID.test(valor);
}

function instante(valor: unknown): valor is string {
  return typeof valor === "string" && valor.length <= 64 &&
    Number.isFinite(Date.parse(valor));
}

/** Limita a superfície de JSON variável antes da validação final no PostgreSQL. */
function jsonLimitado(valor: unknown, profundidade = 0): valor is Json {
  if (profundidade > 10 || valor === null || typeof valor === "boolean") return profundidade <= 10;
  if (typeof valor === "number") return Number.isFinite(valor);
  if (typeof valor === "string") return valor.length <= 5_000;
  if (Array.isArray(valor)) {
    return valor.length <= MAX_CAMPOS_PENDENTES && valor.every((item) => jsonLimitado(item, profundidade + 1));
  }
  if (!objeto(valor)) return false;
  const entradas = Object.entries(valor);
  return entradas.length <= 100 && entradas.every(([chave, item]) =>
    CHAVE_JSON.test(chave) && jsonLimitado(item, profundidade + 1)
  );
}

function camposPendentes(valor: unknown): valor is string[] {
  return Array.isArray(valor) && valor.length <= MAX_CAMPOS_PENDENTES &&
    valor.every((item) => typeof item === "string" && item.length > 0 && item.length <= 500);
}

function validarProposta(destino: string, proposta: unknown): proposta is Record<string, Json> {
  if (!objeto(proposta) || !jsonLimitado(proposta)) return false;
  const contratos: Record<string, { permitidas: string[]; obrigatorias: string[] }> = {
    compras: {
      permitidas: ["operacao_id", "origem_registro", "telegram_msg_id", "obs", "data", "quantidade", "peso_total_kg", "preco_arroba", "valor_total", "prazo_dias"],
      obrigatorias: ["operacao_id", "data", "quantidade", "valor_total"],
    },
    vendas: {
      permitidas: ["data_abate", "cabecas", "peso_carcaca_total", "preco_arroba", "valor_bruto", "funrural", "prazo_recebimento", "romaneio"],
      obrigatorias: ["data_abate", "cabecas", "peso_carcaca_total", "valor_bruto", "prazo_recebimento"],
    },
    pesagens_caderno: {
      permitidas: ["contexto", "data_folha", "peso_kg", "foto_ref", "origem", "conferido", "obs"],
      obrigatorias: ["contexto", "data_folha", "peso_kg"],
    },
    abates: {
      permitidas: ["data_abate", "lote", "cabecas", "peso_liquido_kg", "valor_liquido"],
      obrigatorias: ["data_abate", "lote", "cabecas", "peso_liquido_kg"],
    },
  };
  const contrato = contratos[destino];
  if (!contrato || Object.keys(proposta).some((chave) => !contrato.permitidas.includes(chave))) return false;
  return contrato.obrigatorias.every((chave) => {
    const valor = proposta[chave];
    return valor !== null && valor !== undefined &&
      (typeof valor !== "string" || valor.trim().length > 0);
  });
}

function validarPedidoPreparacao(valor: unknown): valor is Record<string, Json> {
  if (!objeto(valor) || !mesmasChaves(valor, [
    "versao", "target_table", "source_draft_atualizado_em", "source_pending_action_atualizado_em",
    "codigo_sugerido", "dados_revisados", "inferencias", "campos_pendentes", "proposed_record",
  ])) return false;
  return valor.versao === 1 &&
    ["compras", "vendas", "pesagens_caderno", "abates"].includes(String(valor.target_table)) &&
    instante(valor.source_draft_atualizado_em) && instante(valor.source_pending_action_atualizado_em) &&
    texto(valor.codigo_sugerido, 200, true) && objeto(valor.dados_revisados) && jsonLimitado(valor.dados_revisados) &&
    objeto(valor.inferencias) && jsonLimitado(valor.inferencias) && camposPendentes(valor.campos_pendentes) &&
    validarProposta(String(valor.target_table), valor.proposed_record);
}

function validarPedidoCorretivo(valor: unknown): valor is Record<string, Json> {
  if (!objeto(valor) || !mesmasChaves(valor, [
    "versao", "modo", "draft_atualizado_em", "action_atualizado_em", "dados_extraidos",
    "inferencias", "campos_pendentes", "codigo_sugerido", "resumo", "contexto", "motivo",
  ])) return false;
  const contexto = valor.contexto;
  return valor.versao === 1 && ["salvar", "voltar_confirmacao", "rejeitar", "cancelar"].includes(String(valor.modo)) &&
    instante(valor.draft_atualizado_em) && instante(valor.action_atualizado_em) &&
    objeto(valor.dados_extraidos) && jsonLimitado(valor.dados_extraidos) &&
    objeto(valor.inferencias) && jsonLimitado(valor.inferencias) && camposPendentes(valor.campos_pendentes) &&
    texto(valor.codigo_sugerido, 200, true) && texto(valor.resumo, 2_000, true) &&
    texto(valor.motivo, 1_000, true) && objeto(contexto) &&
    mesmasChaves(contexto, [
      "contexto_canonico", "contexto_nome", "origem_canal", "origem_conversa_id", "origem_mensagem_id", "escopo",
    ]) && Object.values(contexto).every((item) => texto(item, 1_000, true));
}

function estadoHumano(estado: unknown, anexadoEm?: unknown): string {
  if (estado === "pendente") return "Investigação pendente";
  if (estado === "em_execucao") return "Investigação em andamento";
  if (estado === "aguardando_retentativa") return "Aguardando nova tentativa";
  if (estado === "concluida" && !anexadoEm) return "Resultado aguardando anexo";
  return "Sem bloqueio de investigação";
}

function estadoPromocaoHumano(estado: unknown): string {
  const estados: Record<string, string> = {
    preparada: "Preparada",
    aguardando_confirmacao: "Aguardando confirmação",
    aprovado_confinex: "Aprovada no Confinex",
    em_revisao: "Em revisão",
    rejeitado: "Rejeitada",
    cancelado: "Cancelada",
  };
  return typeof estado === "string" && estados[estado] ? estados[estado] : "Atualização registrada";
}

function configuracao(): { url: string; anon: string; serviceRole: string } {
  const url = Deno.env.get("SUPABASE_URL");
  const anon = Deno.env.get("SUPABASE_ANON_KEY");
  const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !anon || !serviceRole) throw new ErroPublico(503, "Serviço indisponível.");
  return { url, anon, serviceRole };
}

function bearer(requisicao: Request): string {
  const autorizacao = requisicao.headers.get("authorization") || "";
  const correspondencia = /^Bearer\s+(.+)$/i.exec(autorizacao);
  if (!correspondencia || correspondencia[1].length > 8_192) throw new ErroPublico(401, "Sessão inválida.");
  return correspondencia[1];
}

async function corpoJson(requisicao: Request): Promise<Record<string, Json>> {
  const tamanho = Number(requisicao.headers.get("content-length") || "0");
  if (!Number.isFinite(tamanho) || tamanho > MAX_CORPO_BYTES) throw new ErroPublico(413, "Pedido excede o limite permitido.");
  const bruto = await requisicao.text();
  if (new TextEncoder().encode(bruto).byteLength > MAX_CORPO_BYTES) throw new ErroPublico(413, "Pedido excede o limite permitido.");
  try {
    const valor: unknown = JSON.parse(bruto);
    if (!objeto(valor)) throw new Error("objeto esperado");
    return valor;
  } catch {
    throw new ErroPublico(400, "Pedido inválido.");
  }
}

async function usuarioAutenticado(url: string, anon: string, token: string) {
  const clienteUsuario = createClient(url, anon, {
    auth: { autoRefreshToken: false, persistSession: false },
    global: { headers: { Authorization: `Bearer ${token}` } },
  });
  const { data, error } = await clienteUsuario.auth.getUser();
  if (error || !data.user) throw new ErroPublico(401, "Sessão inválida.");
  return clienteUsuario;
}

/** Usa RLS com o JWT para autorizar a dupla antes de qualquer RPC privilegiada. */
async function autorizarDupla(clienteUsuario: ReturnType<typeof createClient>, draftId: string, actionId: string): Promise<void> {
  const [rascunho, acao] = await Promise.all([
    clienteUsuario.from("operation_drafts").select("id").eq("id", draftId).eq("pending_action_id", actionId).maybeSingle(),
    // `entidade_id` é um vínculo interno do banco. A autorização do usuário
    // confirma apenas que ele pode ler ambos os objetos; a RPC service_role
    // confere o par e o snapshot numa única transação, sem expor esse vínculo
    // ao navegador nem depender de privilégio SELECT sobre a coluna.
    clienteUsuario.from("pending_actions").select("id").eq("id", actionId).maybeSingle(),
  ]);
  if (rascunho.error || acao.error || !rascunho.data || !acao.data) {
    throw new ErroPublico(403, "Você não tem autorização para esta revisão.");
  }
}

async function consultarBloqueios(
  clienteUsuario: ReturnType<typeof createClient>,
  serviceRole: string,
  url: string,
  corpo: Record<string, Json>,
): Promise<Response> {
  if (!mesmasChaves(corpo, ["acao", "revisoes"]) || !Array.isArray(corpo.revisoes) ||
    corpo.revisoes.length > MAX_CONSULTAS) throw new ErroPublico(400, "Pedido inválido.");
  const revisoes = corpo.revisoes;
  if (!revisoes.every((revisao) => objeto(revisao) &&
    mesmasChaves(revisao, ["chave_cliente", "operation_draft_id"]) &&
    (typeof revisao.chave_cliente === "string" || typeof revisao.chave_cliente === "number") &&
    TEXTO_CHAVE.test(String(revisao.chave_cliente)) && uuid(revisao.operation_draft_id))) {
    throw new ErroPublico(400, "Pedido inválido.");
  }
  const ids = [...new Set(revisoes.map((revisao) => String(revisao.operation_draft_id)))];
  if (!ids.length) return resposta({ bloqueios: [] });
  // A consulta do usuário prova acesso a cada rascunho antes de o mediador
  // usar a projeção privada que contém o vínculo técnico.
  const autorizados = await clienteUsuario.from("operation_drafts").select("id").in("id", ids);
  if (autorizados.error || (autorizados.data || []).length !== ids.length ||
    new Set((autorizados.data || []).map((linha) => linha.id)).size !== ids.length) {
    throw new ErroPublico(403, "Você não tem autorização para estas revisões.");
  }
  const service = createClient(url, serviceRole, { auth: { autoRefreshToken: false, persistSession: false } });
  const { data, error } = await service.from("investigacoes_revisao")
    .select("referencia_publica,source_draft_id,fluxo_tipo,estado_execucao,anexado_em")
    .in("source_draft_id", ids)
    .or("estado_execucao.in.(pendente,em_execucao,aguardando_retentativa),and(estado_execucao.eq.concluida,anexado_em.is.null)");
  if (error) throw new ErroPublico(503, "Não foi possível consultar os cruzamentos.");
  return resposta({
    bloqueios: revisoes.map((revisao) => ({
      chave_cliente: revisao.chave_cliente,
      investigacoes: (data || [])
        .filter((linha) => linha.source_draft_id === revisao.operation_draft_id)
        .map((linha) => ({
          referencia_publica: linha.referencia_publica,
          fluxo_tipo: linha.fluxo_tipo,
          estado_execucao: linha.estado_execucao,
          anexado_em: linha.anexado_em,
          estado: estadoHumano(linha.estado_execucao, linha.anexado_em),
        })),
    })),
  });
}

async function prepararPromocao(clienteUsuario: ReturnType<typeof createClient>, serviceRole: string, url: string, corpo: Record<string, Json>): Promise<Response> {
  if (!mesmasChaves(corpo, ["acao", "operation_draft_id", "pending_action_origem_id", "pedido"]) ||
    !uuid(corpo.operation_draft_id) || !uuid(corpo.pending_action_origem_id) || !validarPedidoPreparacao(corpo.pedido)) {
    throw new ErroPublico(400, "Pedido inválido.");
  }
  await autorizarDupla(clienteUsuario, corpo.operation_draft_id, corpo.pending_action_origem_id);
  const service = createClient(url, serviceRole, { auth: { autoRefreshToken: false, persistSession: false } });
  const { data, error } = await service.rpc("preparar_promocao_revisao_investigada", {
    p_operation_draft_id: corpo.operation_draft_id,
    p_pending_action_origem_id: corpo.pending_action_origem_id,
    p_pedido: corpo.pedido,
  });
  if (error || !objeto(data)) throw new ErroPublico(409, "Não foi possível preparar a promoção. Recarregue a revisão e tente novamente.");
  return resposta({
    preparada: data.preparada === true,
    repeticao_idempotente: data.repeticao_idempotente === true,
    estado: estadoPromocaoHumano(data.estado),
  });
}

async function decidirCorretiva(clienteUsuario: ReturnType<typeof createClient>, serviceRole: string, url: string, corpo: Record<string, Json>): Promise<Response> {
  if (!mesmasChaves(corpo, ["acao", "operation_draft_id", "pending_action_id", "pedido"]) ||
    !uuid(corpo.operation_draft_id) || !uuid(corpo.pending_action_id) || !validarPedidoCorretivo(corpo.pedido)) {
    throw new ErroPublico(400, "Pedido inválido.");
  }
  await autorizarDupla(clienteUsuario, corpo.operation_draft_id, corpo.pending_action_id);
  const service = createClient(url, serviceRole, { auth: { autoRefreshToken: false, persistSession: false } });
  const { data, error } = await service.rpc("decidir_revisao_corretiva", {
    p_operation_draft_id: corpo.operation_draft_id,
    p_pending_action_id: corpo.pending_action_id,
    p_pedido: corpo.pedido,
  });
  if (error || !objeto(data)) throw new ErroPublico(409, "Não foi possível registrar a decisão. Recarregue a revisão e tente novamente.");
  return resposta({
    decidida: data.decidida === true,
    repeticao_idempotente: data.repeticao_idempotente === true,
    estado: estadoPromocaoHumano(data.status),
  });
}

Deno.serve(async (requisicao) => {
  if (requisicao.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });
  if (requisicao.method !== "POST") return erro(405, "Método não permitido.");
  try {
    const { url, anon, serviceRole } = configuracao();
    const corpo = await corpoJson(requisicao);
    const acao = corpo.acao;
    if (acao !== "consultar_bloqueios" && acao !== "preparar_promocao" && acao !== "decidir_corretiva") {
      throw new ErroPublico(400, "Ação não permitida.");
    }
    const clienteUsuario = await usuarioAutenticado(url, anon, bearer(requisicao));
    if (acao === "consultar_bloqueios") return await consultarBloqueios(clienteUsuario, serviceRole, url, corpo);
    if (acao === "preparar_promocao") return await prepararPromocao(clienteUsuario, serviceRole, url, corpo);
    return await decidirCorretiva(clienteUsuario, serviceRole, url, corpo);
  } catch (causa) {
    if (causa instanceof ErroPublico) return erro(causa.status, causa.message);
    return erro(500, "Não foi possível concluir a solicitação.");
  }
});
