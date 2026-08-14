# Conciliação privada de negócios pelo WhatsApp

`tools/conciliar_whatsapp_pix.py` pesquisa os históricos locais do Wey e,
quando configurado, o cache histórico local do `wacli`, pelo valor do PIX. Ele
produz candidatos de evidência para as dúvidas da consolidação.
O valor é a primeira chave de investigação porque o comprovante costuma ser
enviado no WhatsApp junto do valor escrito.

A ferramenta aceita formatos brasileiros e variantes sem separador de milhar,
deduplica mensagens copiadas entre sessões e backups, considera nome e data
apenas como reforço e preserva ambiguidades entre conversas distintas. O
relatório informa a mensagem e o arquivo de origem para conferência, mas
mascara a identidade técnica da conversa.

Ela não chama Supabase, Sheets ou APIs operacionais e não possui modo de
execução operacional. A consulta opcional ao `wacli` força simultaneamente a
flag `--read-only` e `WACLI_READONLY=1`: lê apenas o SQLite previamente
sincronizado e não conecta ao WhatsApp. As únicas escritas do conciliador são
os dois arquivos de saída explicitamente indicados.

```bash
python3 tools/conciliar_whatsapp_pix.py \
  --duvidas /caminho/privado/duvidas.json \
  --sessions-dir /caminho/privado/sessoes-wey \
  --sessions-index /caminho/privado/sessoes-wey.json \
  --wacli-bin /usr/local/bin/wacli \
  --wacli-store /caminho/privado/wacli \
  --saida-json /caminho/privado/conciliacao-whatsapp-pix.json \
  --saida-md /caminho/privado/conciliacao-whatsapp-pix.md
```

Formato mínimo de entrada:

```json
{
  "duvidas": [
    {
      "codigo": "NEG-AA-NNN",
      "negocio": "Nome ou referência humana",
      "valores": ["123.456,78"],
      "data": "31/12/2026"
    }
  ]
}
```

O índice opcional de sessões é a saída privada de
`openclaw sessions --agent wey --json`. Ele permite separar conversas sem
publicar JIDs ou telefones. Sem o índice, a ferramenta ainda lê mensagens que
declaram explicitamente origem WhatsApp.

O par `--wacli-bin`/`--wacli-store` também é opcional, mas os dois parâmetros
devem aparecer juntos. Ele amplia a cobertura para o histórico sincronizado,
inclusive mensagens enviadas pelo titular. Autenticação, `sync` e `history
backfill` são etapas administrativas separadas: podem escrever somente no
cache privado do `wacli`; jamais se usa `wacli send` neste fluxo.

No ambiente do Wey, `wey-whatsapp-live-sync.service` mantém `wacli sync
--follow` permanentemente conectado, com presença silenciosa, reconexão sem
prazo e limite de 2 GB. O heartbeat `wey-whatsapp-live-health.timer` verifica a
captura a cada cinco minutos, tenta reiniciá-la e alerta somente se o reparo
falhar. Assim mensagens novas são persistidas na VPS sem depender do Mac.
O heartbeat considera saudável o processo autenticado que mantém o bloqueio
exclusivo do store; o campo `connected` de um `doctor` concorrente não é usado
como gate porque permanece falso enquanto outro processo detém a conexão.

O timer `wey-whatsapp-automation.timer` executa diariamente uma janela de
manutenção exclusiva: pausa a captura contínua, faz uma sincronização `--once`,
executa `tools/orquestrar_conciliacao_whatsapp.py` e sempre solicita a retomada
da captura, inclusive se a conciliação falhar. O orquestrador concilia por
valor, verifica a cobertura, tenta backfill serial e limitado, repete a busca e
grava um relatório privado com perguntas prontas. O timer simples
`wey-whatsapp-cache-sync.timer` deve permanecer desabilitado para não concorrer
pelo mesmo store.
Falhas transitórias de abertura da conexão, inclusive respostas HTTP 502, têm
até três tentativas espaçadas antes de a janela ser marcada como falha.
A retomada aguarda explicitamente o bloqueio do store ser liberado antes de
iniciar o processo contínuo, evitando concorrência residual no encerramento do
backfill.

Uma busca vazia não prova que a mensagem não existe. Antes de classificar um
valor como ausente, confira `wacli history coverage` na conversa candidata. Se
a data mais antiga do cache for posterior à data do negócio, execute um
`history backfill` limitado e serial e repita a busca pelo valor. O histórico
fornecido pelo aparelho é de melhor esforço; sem resposta do aparelho, o caso
fica com cobertura incompleta, nunca como inexistente. As unidades reproduzíveis
ficam em `infra/systemd/wey-whatsapp-automation.*` e
`infra/systemd/wey-whatsapp-live-*`.

O relatório `orquestracao-whatsapp.json` separa três saídas: evidência
encontrada, cobertura ainda incompleta e conversa candidata não localizada. Nos
dois últimos casos ele inclui uma pergunta pronta, mas não a envia. Assim o Wey
tem um próximo passo determinístico sem assumir fatos nem depender do Mac.
`estado-orquestracao-whatsapp.json` registra tentativas por conversa; o ciclo
seguinte prioriza quem recebeu menos tentativas, evitando repetir sempre os
mesmos contatos e garantindo rotação da fila.

## Interpretação

- `encontrado_unico`: um único contexto técnico ou um candidato claramente
  mais forte; ainda exige leitura humana do trecho e do comprovante;
- `ambiguo`: o mesmo valor aparece em mais de uma conversa sem desempate forte;
- `nao_encontrado`: os históricos locais consultados não contêm a variante
  procurada; só é conclusivo depois de validar a cobertura da conversa;
- `sem_valor_para_busca`: a consolidação ainda não fornece valor utilizável.

O cruzamento posterior com Juan/Telegram, extrato, GTA, NF e pesagem acontece
fora desta ferramenta. Nenhum resultado promove ou altera registros.
