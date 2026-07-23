# Memória dos agentes

Atualizado em 2026-07-23. `memorias_agentes` guarda conhecimento reutilizável,
não o estado corrente de uma operação.

## Contrato

Toda memória precisa de:

- `escopo`: alcance funcional, como global, confinamento ou Boi Balança;
- `agente_origem`: Juan, Ceci ou agente que capturou a informação;
- `assunto`: nome humano curto;
- `tipo`: decisão, preferência, regra, exceção ou aprendizado;
- `importancia`: prioridade de recuperação;
- `validade_inicio` e, quando aplicável, `validade_fim`;
- `fonte_tipo` e referência rastreável;
- `status_confirmacao`: pendente, confirmada, rejeitada ou substituída;
- contexto de origem canônico quando a fonte for uma conversa.

O campo `escopo` não deve ser reutilizado para indicar grupo ou conversa
direta. Esse vínculo fica em `contexto_escopo`, sem alterar os valores
históricos do alcance funcional.

## O que não é memória

Quantidade, peso, preço, vencimento, estado de compra/venda, lote, pesagem e
resultado de abate pertencem a rascunho, evento ou tabela operacional. Um
handoff pode transportar esses dados temporariamente, mas não vira fonte
permanente.

Juan pode propor uma memória como `pendente` ao identificar uma regra,
preferência, decisão, exceção ou aprendizado recorrente. Ceci pode consultar
memórias confirmadas e revisar pendências, mas nenhum dos dois deve usar
memória para aprovar promoção ou substituir `operation_drafts`, `eventos` e as
tabelas operacionais.

## Auditoria

```bash
python3 tools/validar_memorias.py
```

O relatório é somente leitura. Ele mostra IDs, assinaturas e problemas, sem
reproduzir o conteúdo. Registros do tipo genérico `contexto` e possíveis fatos
operacionais com números são enviados para revisão; nada é movido ou apagado
automaticamente.

Na auditoria de 2026-07-23, 12 das 16 memórias ficaram conformes. Quatro
registros do tipo `contexto` também apresentaram sinais de fato operacional e
foram mantidos para revisão, sem alteração.
