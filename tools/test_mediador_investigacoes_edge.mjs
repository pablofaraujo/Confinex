import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const origem = await readFile(
  fileURLToPath(new URL("../supabase/functions/investigacoes-mediador/index.ts", import.meta.url)),
  "utf8",
);

test("mediador aceita somente as três ações fechadas", () => {
  assert.match(origem, /type Acao = "consultar_bloqueios" \| "preparar_promocao" \| "decidir_corretiva"/);
  assert.match(origem, /acao !== "consultar_bloqueios" && acao !== "preparar_promocao" && acao !== "decidir_corretiva"/);
  assert.match(origem, /mesmasChaves\(corpo, \["acao", "revisoes"\]\)/);
  assert.match(origem, /mesmasChaves\(corpo, \["acao", "operation_draft_id", "pending_action_origem_id", "pedido"\]\)/);
  assert.match(origem, /mesmasChaves\(corpo, \["acao", "operation_draft_id", "pending_action_id", "pedido"\]\)/);
});

test("JWT é validado com cliente anônimo antes do service_role", () => {
  assert.match(origem, /Deno\.env\.get\("SUPABASE_ANON_KEY"\)/);
  assert.match(origem, /clienteUsuario\.auth\.getUser\(\)/);
  assert.match(origem, /clienteUsuario\.from\("operation_drafts"\)/);
  assert.match(origem, /clienteUsuario\.from\("pending_actions"\)/);
  assert.match(origem, /await autorizarDupla\(clienteUsuario/);
  assert.match(origem, /Deno\.env\.get\("SUPABASE_SERVICE_ROLE_KEY"\)/);
  assert.match(origem, /service\.rpc\("preparar_promocao_revisao_investigada"/);
  assert.match(origem, /service\.rpc\("decidir_revisao_corretiva"/);
  assert.doesNotMatch(origem, /requisicao\.headers\.get\(".*service/i);
  assert.doesNotMatch(
    origem,
    /clienteUsuario\.from\("pending_actions"\)[\s\S]{0,180}\.eq\("entidade_id"/,
    "o vínculo interno entidade_id é validado na RPC service_role, não pelo navegador",
  );
});

test("consulta de bloqueios autoriza todos os rascunhos e devolve somente dados públicos", () => {
  assert.match(origem, /clienteUsuario\.from\("operation_drafts"\)\.select\("id"\)/);
  assert.match(origem, /\(autorizados\.data \|\| \[\]\)\.length !== ids\.length/);
  assert.match(origem, /service\.from\("investigacoes_revisao"\)/);
  assert.match(origem, /chave_cliente: revisao\.chave_cliente/);
  assert.match(origem, /referencia_publica: linha\.referencia_publica/);
  assert.match(origem, /estado: estadoHumano/);
  assert.doesNotMatch(origem, /pending_action_id:\s*data\./);
  assert.doesNotMatch(origem, /operation_draft_id:\s*data\./);
});

test("entrada e erros são limitados e sanitizados", () => {
  assert.match(origem, /const MAX_CORPO_BYTES = 300 \* 1024/);
  assert.match(origem, /MAX_CONSULTAS = 50/);
  assert.match(origem, /jsonLimitado/);
  assert.match(origem, /return erro\(500, "Não foi possível concluir a solicitação\."\)/);
  assert.doesNotMatch(origem, /JSON\.stringify\((?:error|causa)\)|erro:\s*(?:error|causa)\.message/);
});
