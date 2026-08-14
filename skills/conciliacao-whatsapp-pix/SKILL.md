---
name: conciliacao-whatsapp-pix
description: Localiza comprovantes e mensagens de pagamento nos históricos locais do WhatsApp para esclarecer candidatos da consolidação privada, sempre em modo somente leitura.
---

# Conciliação WhatsApp × PIX

Use esta skill quando Pablo pedir para esclarecer, conferir ou sanear dúvidas da
consolidação de negócios usando o histórico do WhatsApp.

## Regra principal

Comece pelo valor do PIX. Pablo normalmente envia o comprovante acompanhado do
valor escrito. Nome, apelido, fazenda e município servem para confirmar o
candidato; não substituem a correspondência financeira e contextual.

## Execução

1. Receba um JSON privado de dúvidas com `codigo`, `negocio`, `valores` e, se
   disponível, `data`.
2. Gere um índice privado das sessões do Wey, sem exibir números de telefone.
3. Confirme que o cache privado do `wacli` está autenticado e sincronizado. A
   autenticação e a sincronização podem escrever somente nesse cache técnico;
   nunca use subcomandos de envio.
   - confira `wacli history coverage` para a conversa candidata;
   - se o início da cobertura for posterior à data do negócio, use
     `wacli history backfill` de forma limitada e serial;
   - repita a busca pelo valor depois do backfill;
   - se o aparelho não devolver o histórico, classifique como cobertura
     incompleta, nunca como evidência de inexistência.
4. Execute:

```bash
python3 /root/ponte/tools/conciliar_whatsapp_pix.py \
  --duvidas /root/.openclaw/workspace-wey/private/duvidas-consolidacao.json \
  --sessions-dir /root/.openclaw/agents/wey/sessions \
  --sessions-index /root/.openclaw/workspace-wey/private/sessoes-wey.json \
  --wacli-bin /usr/local/bin/wacli \
  --wacli-store /root/.local/state/wacli-confinex \
  --saida-json /root/.openclaw/workspace-wey/private/conciliacao-whatsapp-pix.json \
  --saida-md /root/.openclaw/workspace-wey/private/conciliacao-whatsapp-pix.md
```

5. Leia os candidatos mais fortes e confronte valor, data, contraparte,
   comprovante e mensagens próximas.
6. Classifique como evidência suficiente somente quando uma única conversa e o
   contexto fecharem o vínculo. Valor repetido, adiantamento, soma, devolução,
   desconto, frete ou pagamento parcial permanecem ambíguos.
7. Entregue a Pablo um resumo por código com o que foi comprovado e a dúvida
   residual. Não exponha número de telefone, chave bancária ou conversa inteira.

## Limites absolutos

- não enviar, responder, reagir, encaminhar, apagar ou arquivar mensagens;
- não executar `openclaw message send` nem qualquer comando `wacli send`;
- toda busca do `wacli` deve usar `--read-only` e `WACLI_READONLY=1`;
- não gravar no Supabase, Sheets, Obsidian ou tabelas operacionais;
- não criar rascunho, compra, venda, abate, conciliação ou promoção;
- não tratar valor isolado como prova única;
- não pesquisar apenas pelo nome quando houver valor disponível;
- não usar mensagens do Telegram nesta ferramenta. O cruzamento com Juan é uma
  etapa posterior e separada, para preservar a origem de cada evidência.
