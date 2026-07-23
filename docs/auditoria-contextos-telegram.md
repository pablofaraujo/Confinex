# Auditoria de contextos do Telegram

Atualizado em 2026-07-23. Auditoria somente leitura das sessões do Juan, das
auditorias da Ponte e das tabelas relacionadas no Supabase. Nomes privados,
IDs de grupo, valores, trechos de conversa e códigos operacionais específicos
foram mantidos apenas no relatório local em `docs/privado/`.

## Escopo e método

O inventário de 110 arquivos de sessão contém índices, trajetórias e arquivos
auxiliares. Para não contar a mesma conversa mais de uma vez, foram analisados
21 históricos primários: 10 atuais e 11 arquivados após reset ou exclusão.
Esses históricos reúnem 113 mensagens de usuário. Não existem resumos
separados em `openclaw transcripts`; a fonte disponível é o JSONL das sessões.

As menções abaixo são encontradas por palavras operacionais e podem incluir
repetições, correções e exemplos. Elas não representam quantidade de negócios.
Os casos relevantes foram revisados manualmente antes de formar as pendências.

Também foram lidas as cinco auditorias em `/root/ponte/audits`. Elas contêm
645 ocorrências brutas, 120 ocorrências filtradas e 26 candidatos de compra.
Depois de retirar testes, restam 21 candidatos: um associado a Confinamento,
cinco a Boi Balança e 15 sem contexto recuperável. As duas reconciliações
anteriores criaram seis rascunhos e sete ações de revisão, nunca compras
operacionais.

## Cobertura por contexto

| Contexto sanitizado | Históricos / mensagens | Menções encontradas | Situação na fila |
|---|---:|---|---|
| Confinamento | 4 / 66 | 14 de compra, 6 de pesagem e 1 de pendência | 2 rascunhos e 2 ações em revisão; os principais casos de compra já estão cobertos, mas não há evento ou memória vinculados à chave canônica do grupo |
| Boi Balança | 4 / 71 | 6 de compra, 5 de venda/abate, 15 de pesagem e 7 de negócio | 3 rascunhos em revisão; há registros cancelados de testes. Eventos e memórias usam mais de uma forma de identificar o mesmo contexto |
| Histórico legado sem vínculo | 8 / 109 | 6 de compra, 1 de venda/abate, 13 de pesagem, 11 de negócio e 2 de pendência | Parte foi reconciliada por evidência indireta; não é seguro atribuir o restante a um grupo sem nova confirmação |
| Conversa direta | 2 / 48 | 1 consulta de compra e 1 pendência | A consulta relevante já originou revisão no contexto operacional correspondente |
| Contexto de venda/abate sem nome recuperável | 1 / 6 | 1 venda/abate com pesagem detalhada | Não há rascunho, ação, evento ou memória vinculados; é a principal lacuna descoberta |
| Outros grupos sem atividade operacional | 2 / 4 | Nenhuma menção classificada | Nenhuma ação necessária neste recorte |

## Comparação com o Supabase

O snapshot somente leitura encontrou:

- 12 rascunhos: 7 em revisão e 5 cancelados;
- 20 ações: 9 em revisão, 10 canceladas e 1 executada;
- 39 eventos: 36 registrados, 1 pendente, 1 corrigido e 1 cancelado;
- 16 memórias: 6 confirmadas e 10 pendentes de confirmação;
- 1 passagem de contexto ainda aberta;
- 21 compras, das quais 4 não atendem hoje ao conjunto mínimo
  data/quantidade/valor total;
- 9 vendas, todas ainda sem pelo menos um dos campos exigidos pela promoção
  atual;
- nenhuma pesagem de caderno e nenhum abate na respectiva tabela.

Somente três compras possuem origem Telegram identificável diretamente. Vinte
compras não têm peso total e 16 não têm preço por arroba; parte pode usar outra
forma de preço, mas a ausência impede reconciliação automática e precisa ser
tratada como dado a conferir.

A separação de contexto existe nas sessões, porém ainda não é uniforme no
banco. O mesmo grupo aparece como ID puro, prefixo `telegram:`, nome humano ou
chave histórica de arquitetura. Além disso, 30 dos 39 eventos não possuem
contexto de conversa. Dois rascunhos e oito ações também estão sem contexto
canônico.

## Pendências reais

1. Revisar o caso de venda/abate cujo grupo e mensagem foram recuperados na
   segunda leitura das sessões. A proposta privada de rascunho está pronta,
   mas o peso precisa de conferência por ter recebido correções posteriores.
2. Resolver os 7 rascunhos e 9 ações que continuam em revisão, considerando
   que alguns representam o mesmo caso e não devem ser duplicados.
3. Conferir as 4 compras sem o conjunto mínimo e as 9 vendas incompletas.
4. Triar os 15 candidatos antigos sem grupo; sem evidência de origem, manter
   como auditoria e não criar rascunho automaticamente.
5. Confirmar ou rejeitar as 10 memórias pendentes. Fatos de uma operação devem
   ficar em rascunho, evento ou tabela operacional, não somente em memória.
6. Encerrar a passagem de contexto aberta depois de mover suas informações
   duráveis para as fontes corretas. `contexto_handoff` deve representar apenas
   continuidade temporária.
7. Não preencher retroativamente contexto de eventos por aproximação. Um
   vínculo antigo só deve ser corrigido quando houver mensagem, documento ou
   registro que prove o grupo.

## Mensagens que deveriam ter oferecido rascunho

A busca heurística encontrou 48 mensagens operacionais históricas cuja resposta
não continha “rascunho” ou “fila de Revisões”. A revisão manual mostrou três
classes:

- testes explícitos e instruções de manutenção, que não devem criar rascunho;
- repetições de compras, pesagens e documentos que já foram consolidadas pelas
  auditorias e possuem rascunho de reconciliação;
- um caso real de venda/abate ainda sem cobertura na fila.

Também houve respostas antigas dizendo “atualizado” sem evidência de
persistência. O comportamento correto é informar o que foi calculado, declarar
que nada foi salvo e oferecer o rascunho somente no final. As mensagens
auditadas antecedem as correções atuais do roteador, portanto não provam uma
regressão do Juan atual.

## Oportunidades de correção

- Aplicar, após aprovação do dry-run, o contrato único já implementado em
  `tools/contexto_canonico.py`, na migração aditiva e em
  `tools/normalizar_contextos.py`. A simulação encontrou 20 vínculos
  comprováveis e manteve referências ambíguas sem alteração.
- Registrar evento legível sempre que um rascunho for criado, devolvido,
  rejeitado ou promovido.
- Incluir no teste permanente um contrato que rejeite novas gravações com nome
  humano ou prefixos alternativos no campo técnico de conversa.
- Criar uma consulta de qualidade que destaque rascunhos, ações e eventos sem
  contexto, sem tentar corrigi-los automaticamente.
- Tratar memória como regra, decisão ou preferência reutilizável. Números e
  estado de um negócio pertencem à fila e às tabelas operacionais.

## Telecrawl

Telecrawl não é necessário agora. Sessões, arquivos arquivados, auditorias da
Ponte e Supabase foram suficientes para localizar os principais grupos, os
casos já reconciliados e a lacuna ainda aberta. A limitação restante está nos
históricos legados sem vínculo, e instalar um crawler não cria evidência que
não esteja mais disponível.

Reavaliar somente se uma exportação ou API oficial do Telegram conseguir
recuperar mensagens antigas com metadados de grupo que não aparecem nos
arquivos atuais. Antes disso, adicionar outra ferramenta aumentaria
complexidade sem resolver a normalização de contexto no banco.

## Comandos e validações

Foram usados somente comandos de leitura:

```bash
openclaw sessions --agent juan --limit all --json
openclaw transcripts list --json
find /root/.openclaw/agents/juan/sessions -maxdepth 1 -type f
find /root/ponte/audits -maxdepth 2 -type f
openclaw directory groups list --channel telegram --json
```

As tabelas foram consultadas pela API REST com `GET`, paginação de mil registros
e `select`; nenhum `POST`, `PATCH`, `DELETE` ou RPC foi executado. As assinaturas
SHA-256 dos IDs das onze tabelas auditadas foram comparadas antes e depois e
permaneceram idênticas.
