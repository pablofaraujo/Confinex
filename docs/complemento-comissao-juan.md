# Comissão posterior em rascunho de compra — preparação

## Estado em 06/09/2026

**PREPARADO E TESTADO LOCALMENTE; NÃO APLICADO NEM ATIVADO.**

A recuperação de histórico/consulta atual é uma etapa separada, já descrita em
`continuidade-juan.md`. Encontrar um negócio não equivale a alterar seus dados.
Este ciclo prepara apenas comissão em rascunho pré-operacional: não trata compra
já realizada, pagamento, frete, correção fiscal ou promoção. Nenhum dado privado
é incluído no repositório. A autorização deste ciclo exclui aplicar migrações.

## Fluxo pretendido e fronteiras

```text
Mensagem humana autenticada no grupo
  → recuperar histórico + consultar registros atuais (somente leitura)
  → se houver ambiguidade, apresentar candidatos e exigir escolha explícita
  → conferir par rascunho/pendência, grupo, estado e base do vendedor
  → prévia humana com comissão separada e validade de 15 minutos
  → nova confirmação do mesmo responsável/grupo, vinculada à prévia
  → mediador valida identidade atual + autorização do responsável + HMAC
  → RPC: conferir snapshots, atualizar par e registrar evento atomicamente
  → resposta baseada no resultado confirmado, sem promoção operacional
```

As etapas após a prévia **não estão conectadas ao runtime**. O módulo puro
não lê histórico/banco sozinho, não escolhe entre homônimos e não autentica
uma pessoa por receber um objeto com IDs. A identidade precisa ser extraída
pelo futuro mediador de uma entrada autenticada, nunca do modelo, do histórico,
de mensagem encaminhada ou de texto fornecido pelo usuário. Tópicos e outros
canais são recusados nesta versão delimitada. O responsável também precisa
estar autorizado no grupo; igualdade entre IDs declarados não prova isso.

## Contrato implementado em código, sem executor

`tools/complemento_comissao_juan.py`:

- seleção única ou escolha explícita entre candidatos apresentados;
- par vinculado, no mesmo contexto canônico Telegram e ainda em revisão;
- `valor_total` existente é a base do vendedor, com até duas casas efetivas;
- percentual positivo até 100%, com quatro casas efetivas; cálculo decimal,
  arredondado uma única vez para centavos (meio centavo arredonda para cima);
- percentual/valor finais substituem a comissão anterior, nunca são somados
  repetidamente. A prévia avisa quando há substituição;
- preço, quantidade, peso, origem e valor do vendedor permanecem preservados;
- prévia assinada com HMAC e hash integral; a frase curta só identifica a
  prévia, não substitui HMAC/autenticação;
- confirmação nova, literal, no mesmo grupo e pelo mesmo responsável,
  distinta do pedido e das mensagens originais; validade de 15 minutos;
- retorno é apenas um contrato de chamada, sem rede, banco ou executor.

Não há busca de aproximação para fabricar base ausente: compra operacional,
base incompleta, estados encerrados, links contraditórios e dados divergentes
entre rascunho e pendência recusam a prévia.

## Migração proposta — não aplicar automaticamente

Arquivo: `supabase/migrations/202609060001_complemento_comissao_rascunho.sql`.

Cria `confirmar_comissao_rascunho_juan`, duas funções de proteção e três
triggers novos. O schema privado `juan_comissao_privado` contém `autorizacoes`,
uma tabela sem acesso para PUBLIC, anon, authenticated ou service_role. Suas
capacidades incluem transação, processo, recurso, ID e retrato final exato;
são criadas e consumidas integralmente dentro da transação da RPC. Não são
novos registros operacionais nem uma fila que precise de limpeza periódica.
Não há backfill, troca de função existente, alteração de RLS existente ou
instalação de trigger nas tabelas operacionais. Reaplicação deve ser recusada
por objetos já existentes, não sobrescrevê-los silenciosamente.

A RPC é exclusiva de service_role, mas **isso sozinho não isola o mediador**.
Se um worker/modelo conseguir chamar RPC arbitrária com essa credencial,
poderá inventar autoria. A futura rota deve isolar credencial, validar HMAC e
identidade/capacidade do canal e permitir apenas esta operação. Não expor
uma RPC genérica na ponte nem adicionar a chave ao sandbox do agente.

O SQL recompõe o cálculo sobre o retrato travado, compara o snapshot completo
(timestamps comparados por instante), bloqueia promoção/investigação ligada
e escreve apenas dados extraídos/estado/relógio do par e um evento. O evento
guarda antes/depois, autoria e origem. O ID é determinístico por grupo e nova
mensagem; repetição do mesmo envelope retorna o recibo sem repetir alteração.
Mesma mensagem com outro conteúdo é conflito. Replay exige também a proveniência
completa do evento, não apenas seu tipo ou um JSON parecido.

Proteções persistentes do novo marcador `comissao-juan-v1`:

- aba antiga não apaga a comissão, sua base ou seu vínculo após a confirmação;
- o par fica congelado contra edição/remoção pelo fluxo legado (incluindo
  rejeição/cancelamento), exceto no-op e a própria RPC auditada;
- promoção compartilha a trava do rascunho e é recusada enquanto não existir
  mapeamento operacional homologado da comissão;
- marcador inexistente em registros legados não ativa esse congelamento.

Essas restrições são deliberadas no artefato **inativo**. Antes de habilitar
usuários reais, implementar/homologar edição posterior no mediador e na tela
para preservar a possibilidade de salvar ajustes. A comissão não é um custo
operacional persistido em `compras` nesta etapa, e não pode ser descartada
pelo promotor atual. Não prometer fechamento do fluxo completo.

## Testes permanentes

Sem Supabase, Telegram ou dados reais:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_complemento_comissao_juan.py' -v
python3 tools/test_ecossistema.py
python3 tools/test_complemento_comissao_postgres.py --obrigatorio
git diff --check
```

O último teste exige um PostgreSQL **descartável local**, com CLI e permissão
para criar bancos/papéis de teste. `PGHOST` remoto/serviço alternativo é recusado.
Usar cluster isolado, nunca apontar variáveis para produção. O runner cria e
remove seu próprio banco, reaproveitando a fundação e as guardas reais das
migrações de investigações. O CI roda o mesmo teste no PostgreSQL do job.

Cobertura: ambiguidade, prévia humana, números inválidos, base preservada,
substituição não acumulativa, HMAC, origem/autor/frase/validade; instalação
sem alteração de dados/RLS/funções existentes; privilégios fechados; CAS;
falha no segundo UPDATE e timeout revertendo também o primeiro; replay;
duas confirmações iguais/divergentes em conexões distintas; cliente antigo;
promoção sem destino bloqueada; capacidade privada sem sobras.

Teste simulado não é prova de entrada humana real no Telegram. Bot enviar
mensagem a outro bot não comprova recebimento humano. Não foram enviados
testes Telegram neste ciclo; não houve nova inferência externa, mudança ou
reinício na VPS. A leitura do Supabase serve somente para inventário/snapshots.

## Aplicação, reversão e próximos gates

1. Revisar SQL e hashes publicados, conferir catálogo real/efeito dos novos
   triggers e obter **autorização específica da migração**. Ela não está
   implicitamente autorizada por aprovar este desenvolvimento.
2. Capturar snapshots antes/depois da instalação autorizada; ela não altera
   registros existentes. Manter mediador/rota desligados.
3. Implementar mediador autenticado e isolado, edição posterior e visualização
   da comissão, com testes positivos e negativos. Definir separadamente o
   destino operacional, sem reaproveitar `valor_total` para somar comissão.
4. Fazer prova de canal/confirmação real somente quando a escrita puder ser
   isolada. O congelamento também impede apagar fixtures persistentes: **não
   criar testes no Supabase neste estágio** nem adicionar bypass de limpeza.

Reversão antes de qualquer uso: desligar a rota (já desligada nesta etapa),
confirmar por leitura que não existem marcadores/recibos deste contrato e que
a tabela de capacidades está vazia; com autorização própria, retirar somente
os três triggers/funções e o schema privado recém-criados, sem `CASCADE`.
Depois de uso real, não retirar guardas nem apagar eventos: preservar fontes
e fazer um plano auditado de reversão por registro. Reverter o commit de
código não reverte banco, e publicar o commit não aplica a migração.
