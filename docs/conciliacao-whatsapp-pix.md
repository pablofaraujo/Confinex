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

No ambiente do Wey, o timer `wey-whatsapp-cache-sync.timer` executa diariamente
uma sincronização `--once`, com presença silenciosa, limite de 2 GB e sem
webhook. O systemd impede duas instâncias simultâneas do mesmo serviço. Esse
timer atualiza somente o cache técnico; a conciliação continua sendo uma etapa
separada e estritamente `--read-only`.

Uma busca vazia não prova que a mensagem não existe. Antes de classificar um
valor como ausente, confira `wacli history coverage` na conversa candidata. Se
a data mais antiga do cache for posterior à data do negócio, execute um
`history backfill` limitado e serial e repita a busca pelo valor. O histórico
fornecido pelo aparelho é de melhor esforço; sem resposta do aparelho, o caso
fica com cobertura incompleta, nunca como inexistente. As unidades reproduzíveis
do timer ficam em `infra/systemd/wey-whatsapp-cache-sync.*`.

## Interpretação

- `encontrado_unico`: um único contexto técnico ou um candidato claramente
  mais forte; ainda exige leitura humana do trecho e do comprovante;
- `ambiguo`: o mesmo valor aparece em mais de uma conversa sem desempate forte;
- `nao_encontrado`: os históricos locais consultados não contêm a variante
  procurada; só é conclusivo depois de validar a cobertura da conversa;
- `sem_valor_para_busca`: a consolidação ainda não fornece valor utilizável.

O cruzamento posterior com Juan/Telegram, extrato, GTA, NF e pesagem acontece
fora desta ferramenta. Nenhum resultado promove ou altera registros.
