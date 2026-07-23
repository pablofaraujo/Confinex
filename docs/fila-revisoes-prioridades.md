# Prioridade e saneamento da fila de Revisões

Atualizado em 2026-07-23. O inventário desta página é somente leitura e não
autoriza promoção, cancelamento, rejeição ou correção automática.

## Inventário sanitizado

O snapshot auditado encontrou 13 rascunhos, 21 pendências, 40 eventos e 16
memórias. Entre os rascunhos, oito estão abertos e cinco encerrados. A fila
ativa está distribuída assim:

| Contexto | Prioridade alta | Prioridade média |
|---|---:|---:|
| Fazenda Operacional | 1 | 0 |
| Boi Balança | 1 | 2 |
| Confinamento | 0 | 3 |
| Contexto ainda não identificado | 0 | 1 |

Dois itens ativos precisam de conferência prioritária: os casos de
venda/abate. Seis compras abertas ficaram na prioridade média. Sete itens
abertos ainda têm campos obrigatórios faltantes. Todos os 13 rascunhos foram
criados nos últimos sete dias na data deste levantamento.

## Como a prioridade funciona

A prioridade é uma ajuda de ordenação, não uma aprovação:

- venda ou abate começa em prioridade alta;
- compra e pesagem começam em prioridade média;
- valor, pagamento ou recebimento pendente aumentam a prioridade;
- erro de gravação aumenta a prioridade;
- item antigo sobe gradualmente;
- origem sem mensagem reduz a confiança e exige conferência.

A tela mostra “Prioridade alta”, “Prioridade média” ou “Prioridade baixa”,
ordena primeiro o que está aberto e tem maior impacto e oferece o filtro
“Precisa da sua conferência”. O cálculo usa apenas nomes humanos; identificador
técnico de conversa continua oculto.

## Plano dry-run de saneamento

Não foi encontrada duplicidade exata por mesma conversa, mensagem e tipo.
Foram encontrados:

- seis rascunhos ativos sem pendência diretamente ligada;
- sete pendências ativas que parecem corresponder a rascunhos, mas não têm
  vínculo comprovado;
- três rascunhos sem evento ligado pelo ID, todos já cancelados;
- nenhum evento de revisão com referência explicitamente quebrada;
- 37 eventos sem contexto canônico completo.

O plano é conservador:

- manter registros encerrados como histórico;
- vincular rascunho e pendência somente após comparar origem, mensagem,
  operação e conteúdo;
- não criar evento retroativo para item cancelado sem necessidade operacional;
- marcar para revisão os eventos sem contexto, sem inferir grupo;
- não corrigir os 25 casos ambíguos preservados pela normalização;
- não transformar memória operacional em novo rascunho quando já existir
  rascunho ou evento cobrindo o fato.

Os IDs e pares prováveis ficam somente em
`docs/privado/saneamento-fila-2026-07-23.md`.

## Campos guiados

Para venda/abate, a tela explica diretamente:

- “Falta peso para calcular e conferir a venda”;
- “Confira o valor bruto antes de promover”;
- “Falta previsão de recebimento”.

Essas faltas bloqueiam apenas a preparação da promoção. “Salvar ajustes”
continua disponível.
