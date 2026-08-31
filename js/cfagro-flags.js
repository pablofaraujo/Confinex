// Flags de rollout do ecossistema CFAgro. Arquivo externo por contrato:
// revisoes.html não admite script inline (tools/test_revisoes_frontend.js).
//
// CFAGRO_INVESTIGACOES_ATIVAS — ligada na janela única de 31/08/2026, após:
// migração 202608290002 aplicada (gate de atestação verde), mediador
// `investigacoes-mediador` ACTIVE com verify_jwt e broker isolado atestado.
// Desligar esta flag é o PRIMEIRO passo de qualquer reversão (antes do
// rollback SQL), conforme docs/investigacoes-proativas.md.
globalThis.CFAGRO_INVESTIGACOES_ATIVAS = true;
