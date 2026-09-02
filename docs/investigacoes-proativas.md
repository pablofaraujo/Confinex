# Investigações proativas antes da revisão

Esta camada cruza evidências disponíveis antes de apresentar uma dúvida na fila
de Revisões. O objetivo é reduzir perguntas que o próprio ecossistema consegue
responder, sem transformar uma hipótese em dado confirmado e sem promover uma
operação.

## Fluxo seguro

```text
Agronotas / IMA / OFX / Telegram / Wey
                  ↓
        adaptadores somente leitura
                  ↓
   staging e evidências canônicas existentes
                  ↓
        central de investigações
                  ↓
 RPC atômica do materializador canônico
                  ↓
 tripla vinculada + evidências anexadas antes de aparecer
                  ↓
       revisão humana e fluxo já homologado
```

As tabelas `fontes_importacao`, `negocios_candidatos`, `negocio_versoes`,
`evidencias_negocio`, `transacoes_banco_staging`,
`conciliacoes_candidatas` e `vinculos_documentais_candidatos` continuam sendo
as fontes canônicas de preparação. A investigação coordena o trabalho e aponta
para essas evidências; ela não copia XML, OFX, conversa ou documento bruto.

## Responsabilidades

- `investigacoes_revisao`: assunto, origem, revisão-base, política, prioridade,
  estado da execução e resultado da rodada;
- `investigacao_tarefas`: consultas normalizadas por adaptador e uma tarefa
  interna de síntese, com cobertura, tentativas, próxima execução, lease e
  fencing token;
- `investigacao_evidencias`: referência à fonte canônica, chave natural,
  linhagem e resumo mínimo sanitizado;
- `investigacao_alternativas`: versões explicáveis, confiança por campo,
  justificativa humana e retrato dos campos propostos;
- `investigacao_alternativa_evidencias`: ligação explícita das evidências
  favoráveis e contrárias a cada versão;
- `investigacao_pendencias`: dado ausente, divergência, cobertura incompleta,
  fonte indisponível ou decisão humana necessária;
- `investigacao_eventos`: trilha técnica append-only;
- `investigacao_entregas`: estado mutável de entrega da outbox, separado do
  evento que nunca é sobrescrito.

Estado de execução e resultado são eixos independentes. Uma rodada
`concluida` pode terminar em `alternativas_multiplas`, `divergente`,
`evidencia_insuficiente` ou `cobertura_incompleta`; concluída não significa
confirmada.

## Correlação e confiança

Chaves exatas e verificáveis, como NF, GTA, conta + FITID, referência B3,
mensagem e hash de anexo, orientam a correlação determinística. Valor, data e
contraparte únicos podem formar uma alternativa provável, mas não uma
confirmação. Nome ou valor isolado é apenas pista.

Fontes derivadas do mesmo documento pertencem à mesma linhagem. O XML de uma
NF e a leitura desse mesmo XML pelo Agronotas não contam como duas confirmações.
Ambiguidade, divergência central, conta incompatível, aritmética inconsistente
ou cobertura incompleta impedem confiança forte. A confiança é registrada por
campo e por alternativa; a classificação geral não pode superar o campo
obrigatório mais fraco.

Um rótulo de versão ou grupo fornecido por um adaptador é apenas pista: ele
preserva os campos juntos para comparação, mas recebe limite de confiança e
não pode, sozinho, formar uma alternativa única. Pistas sem grupo nunca são
descartadas quando outra versão possui rótulo. Uma fonte declarada como
`vazio_com_cobertura` não pode publicar candidatos, e duas opções conflitantes
não recebem selo forte mesmo que cada uma possua identificadores exatos. A
regra determinística vigente é `confianca-deterministica-v2`.

O modelo de linguagem pode extrair campos e redigir uma explicação humana em
estrutura validada. Ele não escolhe vínculos, não calcula a confiança, não muda
estado e não opera ferramentas livremente. Sua saída permanece uma alegação
ligada às evidências, nunca um fato confirmado.

## Idempotência e concorrência

- investigação: assunto + origem + fingerprint da revisão-base + versão da
  política;
- tarefa: investigação + adaptador + hash da consulta + versão do adaptador;
- evidência: chave natural da fonte e sua linhagem;
- anexo: investigação + rascunho canônico + retrato da última edição do
  rascunho.

O trabalhador assume primeiro as tarefas-fonte e, quando todas terminam, uma
tarefa interna `sintese` agrega alternativas e pendências sem se passar por uma
fonte. O estado de cobertura não participa da identidade da tarefa: mudar de
falha para sucesso atualiza o resultado da mesma consulta, não cria outra
tarefa. Cada candidato herda a linhagem e o tipo da fonte quando o adaptador
não os repete no fato normalizado.

Cada item do plano persiste um manifesto fechado com oito campos: referência
do item, adaptador e versão, referência e versão da consulta, especificação
normalizada, forma canônica e hash. O worker reconstrói a consulta somente
desse manifesto e confere referência, JSON canônico e SHA-256 antes de ler a
fonte. O plano precisa estar totalmente materializado e conter exatamente uma
síntese. A cobertura final é derivada de todas as tarefas-fonte; a síntese não
pode declarar uma cobertura mais favorável que as buscas reais.

O trabalhador assume tarefas com lease e fencing token crescente. Lease
vencido pode ser retomado; qualquer resultado do trabalhador anterior é
mantido apenas na trilha privada. Evidências, alternativas e pendências são
versionadas pela tentativa; a conclusão da tarefa publica atomicamente qual
par `lease + fencing` foi aceito. Views e anexação leem somente essa tentativa
concluída e ignoram linhas parciais deixadas por um crash. O executor deve
deixar o banco gerar novos IDs físicos a cada tentativa; os IDs determinísticos
do plano Python são apenas identidades lógicas para a simulação e precisam ser
remapeados dentro da tentativa real. O vínculo entre alternativa e evidência
também exige que a síntese ainda possua o lease e que a evidência pertença à
tentativa concluída da tarefa-fonte. Resultado que chega depois de uma edição humana fica
obsoleto porque a RPC compara `source_draft_atualizado_em` sob bloqueio e não
sobrescreve o formulário. Evidência posterior
a uma revisão encerrada cria investigação complementar; a revisão encerrada
nunca é reaberta automaticamente.

Se o formulário for editado entre o retrato inicial e o anexo, o resultado não
é aplicado nem bloqueia a fila para sempre. Uma RPC exclusiva do mediador prova
o snapshot anterior, o maior fencing token e a nova versão do rascunho sob
lock; então marca pai e tarefas como `obsoleta` e acrescenta um evento técnico.
As evidências antigas continuam privadas para auditoria e uma nova rodada deve
usar o formulário atualizado. A repetição da transição é idempotente.

Somente `tools/materializar_revisoes_staging.py` define a tripla
`operation_drafts`, `pending_actions` e `eventos`; a central não possui um
segundo materializador. Com `--exigir-investigacao`, o executor associa o grupo
completo de candidatos e seu fingerprint a uma investigação concluída e chama
uma única RPC `materializar_revisao_investigada`. A RPC cria a tripla, confere
todos os IDs e timestamps do grupo sob bloqueio, vincula e anexa as evidências
na mesma transação. Qualquer conflito desfaz tudo, portanto o rascunho nunca
aparece sem o resultado que deveria antecedê-lo. O caminho protegido não faz
os três `POST` diretos do modo legado.

O vínculo singular permanece apenas como compatibilidade e aponta o membro
principal ordenado. `negocio_candidato_ids`, o mapa de timestamps e
`fingerprint_grupo` preservam todos os membros de uma duplicidade aparente;
uma alteração em qualquer membro invalida o snapshot inteiro. Esses metadados
ficam fora das versões humanas e a interface também os remove defensivamente.

`vincular_investigacao_rascunho` e `anexar_investigacao_revisao` são etapas
internas da transação protegida. Nenhuma delas recebe tabela operacional e não
existe caminho para `operacoes`, `compras`, `vendas`, `abates`,
`pesagens_caderno`, `fluxo_caixa` ou `transacoes_banco`.

A promoção possui uma segunda barreira no banco depois da ativação controlada.
Triggers em
`operation_drafts` e `pending_actions` usam as mesmas travas consultivas da
investigação e recusam preparação por cliente antigo, chamada direta ou tela
desatualizada enquanto houver investigação ativa ou concluída ainda não
anexada. Esses guardiões pertencem à migração de ativação `202608290002`, não à
fundação em sombra.

Uma promoção que chega a um estado terminal não reabre nem sobrescreve as
investigações complementares que ficaram para trás. Após a ativação de
`202608290002`, o trigger terminal grava somente uma solicitação idempotente em
`investigacao_sucessoes_pendentes`. O consumo distingue dois casos:

- no fluxo `sem_gravacao`/`pre_revisao`, a sucessora pode ser criada a partir
  do retrato já selado, inclusive quando a origem é apenas um candidato e ainda
  não existe rascunho;
- no fluxo `com_gravacao`/`corretiva`, o item fica
  `aguardando_planejamento`. Um planejador precisa buscar um contexto
  sanitizado, reconstruir explicitamente as fontes e submeter o novo plano com
  comparação e troca (CAS). Não existe clonagem silenciosa do plano anterior.

O contexto entregue ao planejador contém somente a referência pública do
assunto, origem sanitizada, referências de consulta, cobertura incompleta,
estado humano materializado e um horário de referência determinístico. IDs
técnicos, payload operacional e conteúdo bruto não são expostos como entrada
livre. A repetição confere a semântica completa da sucessora já persistida e
retorna o mesmo mapa, sem consultar novamente uma fonte que pode ter mudado.

Uma investigação corretiva que ficou obsoleta porque o registro operacional
mudou segue o mesmo princípio. A RPC legada
`substituir_investigacao_corretiva_stale` falha fechada com
`PLANEJAMENTO_FONTES_NECESSARIO`. O caminho permitido é obter o contexto
selado por `obter_contexto_replanejamento_corretiva_stale` e criar uma nova
geração por `replanejar_investigacao_corretiva_stale`. Se a revisão humana
antiga já foi materializada, ela e sua pendência são canceladas com evento
auditável; nada é apagado, promovido ou alterado na tabela operacional.

Essas transições usam uma ordem única de locks — rascunho, candidatos,
promoção, pendência, investigação, tarefas e registro operacional conforme o
caso —, hash do retrato atual, fencing e CAS. Mudança de vínculo, conteúdo,
estado humano, identidade ou snapshot entre leitura e escrita aborta a
transação e exige novo planejamento.

A preparação protegida também possui a RPC
`preparar_promocao_revisao_investigada`, que atualiza rascunho e pendência de
origem e cria ação de promoção e evento numa única transação idempotente. Ela é
`SECURITY DEFINER` com `search_path` fixo, owner atestado e ACL exclusiva para
`service_role`; aceita um contrato fechado e só pode ser executada pelo mediador.
O gate de ativação falha se owner, ACL, RLS, policies ou privilégios de tabela
divergirem do inventário esperado. O navegador **não** recebe essa permissão
nem essa credencial.
Antes de ativar a flag será obrigatório colocar um mediador autenticado no
servidor, validar que o usuário pode decidir sobre os dois registros e remover
dele metadados técnicos que não fazem parte do pedido. Enquanto esse mediador
não existir, a tela com a flag ativa bloqueia a preparação antes de qualquer
escrita e mantém **Salvar ajustes** disponível; não existe fallback para as
quatro escritas do cliente antigo.

## Privacidade e isolamento

Cada adaptador usa credencial somente leitura e falha de forma independente.
Conteúdo integral do WhatsApp permanece no cache privado do Wey; o Supabase
recebe apenas referência opaca, hash ou HMAC e resumo mínimo. Telefone, JID,
texto integral, token, documento e valor sensível não aparecem em logs ou no
frontend.

As oito tabelas-base desta camada são privadas ao executor. O usuário
autenticado consulta somente as views `v_investigacoes_revisao`,
`v_investigacao_alternativas`, `v_investigacao_evidencias` e
`v_investigacao_pendencias`, que não projetam conversa, mensagem, chave natural,
lease, erro técnico, referência privada nem valores dos snapshots. Valores e
justificativas detalhadas só chegam ao rascunho já protegido pelo fluxo de
Revisões. A própria migração consulta os
privilégios efetivos e falha se `authenticated` puder ler uma tabela-base ou se
o executor puder alterar ou apagar a trilha append-only.

Os JSONs publicáveis também possuem uma restrição recursiva no banco. Chaves
de credencial, conteúdo bruto, conversa, mensagem, JID e contato, além de
valores com token, e-mail ou documento numérico, são recusados mesmo que um
futuro adaptador deixe de aplicar a sanitização do cliente. Identificadores em
qualquer formato UUID canônico — inclusive v7, nulo e derivados de hash — são
ocultados. Chaves não ASCII, campos obrigatórios vazios, nulos ou duplicados,
grupos sem candidato principal e timestamps inválidos também falham fechados.

O resultado de uma fonte distingue `vazio_com_cobertura`,
`cobertura_incompleta`, `indisponivel`, `reautenticacao_necessaria` e
`erro_permanente`. Uma busca vazia sem cobertura suficiente nunca significa
que a informação não existe.

## Execução e observabilidade

- trigger terminal, somente depois de `202608290002`: grava uma entrada
  idempotente na outbox de sucessões;
- timer: processa retentativas, backfills e novas evidências;
- heartbeat: verifica processo, autenticação, idade da fila, leases e frescor;
  nunca executa trabalho de negócio.

Leituras podem repetir com backoff curto. Escritas não recebem repetição
automática; uma resposta incerta é reconciliada pela chave idempotente. A
única exceção conhecida no ecossistema é `conclude_lease_v1` em
`tools/promocao_operacional.py`: ela repete até três vezes a RPC
`concluir_promocao_operacional` somente em falha de transporte, o que é
seguro porque a RPC é idempotente por lease e fencing token e a repetição
exata do mesmo pedido terminal retorna `repeticao_idempotente`. Logs
sanitizados registram apenas correlation ID, adaptador, estado, cobertura,
duração e código de erro.

## Implantação gradual

1. versionar e revisar as duas migrações e o rollback, sem aplicar nenhuma;
2. conferir por catálogo que `pgcrypto.digest` está no schema `extensions`, que
   `service_role` receberá apenas `USAGE` e `EXECUTE` mínimos e capturar snapshot
   do schema e das políticas;
3. mediante autorização separada, aplicar somente
   `202608290001_investigacoes_revisao.sql`; a flag e o mediador permanecem
   desligados, e o fluxo legado precisa continuar funcionando;
4. rodar em sombra, apenas leitura, com uma fonte e um contexto, sem
   materialização nem mudança operacional;
5. provar as RPCs em PostgreSQL 15 e homologação: contrato, concorrência,
   fencing, RLS, edição durante a rodada, idempotência e rollback;
6. implantar o mediador autenticado sem tráfego e provar autorização,
   sanitização e ausência de credencial privilegiada no navegador;
7. numa única janela de ativação, aplicar
   `202608290002_ativar_mediador_investigacoes.sql`, ativar a rota protegida e
   só então ligar a flag da interface. Nunca aplicar `0002` com a rota legada
   ativa e nunca ligar a flag sem o mediador;
8. provar navegação, preparação atômica, bloqueio de fallback e reversão;
9. depois habilitar um planejador/consumidor com limite pequeno; provar o
   replanejamento de sucessões terminais e corretivas stale; por último,
   habilitar timer e novos adaptadores um de cada vez.

Antes da ativação, a reversão é apenas desligar worker, flag e mediador; a
fundação aditiva pode permanecer vazia. Depois de `0002`, não se desliga o
mediador isoladamente. Primeiro interrompe-se o tráfego da rota protegida e
desliga-se a flag; em seguida executa-se o rollback revisado
`supabase/rollbacks/202608290002_desativar_mediador_investigacoes.sql`, que
remove os três gatilhos do mediador e os quatro guardiões de vínculo operacional
instalados pelo cutover, preserva os dois guardiões corretivos permanentes e
restaura exatamente a política ampla anterior de `pending_actions`; só então o
frontend legado pode voltar. A fundação `0001`, isoladamente, não instala os
quatro guardiões operacionais. O teste PostgreSQL aplica ativação e rollback
duas vezes e comprova INSERT vinculado legado nos quatro destinos tanto após
somente `0001` quanto depois da reversão.
As tabelas, evidências e eventos da fundação não são apagados.

## Estado desta entrega

As migrações `202608290001_investigacoes_revisao.sql` (fundação) e
`202608290002_ativar_mediador_investigacoes.sql` (guardas, RLS e outbox
terminal) foram aplicadas em produção na janela única de 30–31/08/2026, com o
mediador ativo e a flag da interface ligada; o rollback de `0002` permanece
versionado e testado. O consumidor do broker roda apenas em dry-run e nada é
publicado por ele. `tools/planejador_investigacoes.py` está versionado e
testado (dry-run por padrão, limite 1–10, confirmação por hash, escrita
somente em `investigacoes_revisao` e `investigacao_tarefas`, reparo idempotente
de investigação que ficou sem a tarefa de fonte); os payloads reais que ele
gera são provados contra o schema pós-`0002` em
`tools/test_migracao_postgres.py`. `tools/worker_fonte_outro.py` também está
versionado e testado: o worker de fonte do adaptador `outro` fala apenas com o
socket local do broker e lê um snapshot local somente leitura
(`tools/exportar_snapshot_consolidacao.py`); a correspondência é
determinística e conservadora (pista nível "possível", nunca vínculo nem
confiança forte), a cobertura é honesta (`indisponivel`/`vazio_com_cobertura`)
e o atestado HMAC é assinado localmente sem que o segredo saia do processo. O
ciclo completo — planejador cria a investigação, `assumir` entrega a tarefa, o
worker monta e assina, `publicar` aceita e a repetição é idempotente — é
provado de ponta a ponta no PostgreSQL efêmero pelo mesmo harness.
`tools/executor_sintese.py` fecha a rodada: materializa idempotentemente a
linha da tarefa de síntese fiel ao plano imutável, assume pela RPC (síntese
não usa credencial de adaptador — o banco deriva e confere a cobertura) e,
na v1, publica somente o caso sem evidências utilizáveis (nenhuma
alternativa; uma pendência aberta por campo obrigatório), concluindo a
investigação como `evidencia_insuficiente`/`cobertura_incompleta`; com
evidências presentes a v1 aborta em vez de concluir descartando-as (a
montagem de alternativas explicáveis é a v2). O ciclo com conclusão —
planejador → fonte vazia → síntese → investigação concluída com pendências —
também é provado no harness. O 1º ciclo REAL rodou em produção em 02/09/2026
(investigação do rascunho CF-26-012, fonte `vazio_com_cobertura` publicada
com atestado, broker fora do dry-run só durante a janela autorizada e
restaurado). Não há timer nem worker rodando continuamente; cada execução e
cada ativação seguem exigindo autorização própria (passo 9).
Os contratos locais e os testes funcionam sem rede e sem chamada real ao
Supabase. A flag permanece desligada por padrão. Quando for homologada, a tela
falhará fechada se a view não puder ser consultada, agrupará todas as
investigações do rascunho e ocultará IDs, timestamps e fingerprints. **Salvar
ajustes** continua disponível. A preparação permanece bloqueada até existir e
ser homologado o mediador autenticado que chama a RPC transacional. Nenhuma
credencial privilegiada deve ser incorporada ao JavaScript publicado.
