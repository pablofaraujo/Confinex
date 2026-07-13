# Histórico de evolução do projeto (git log, 31 commits)

1. **Origem**: upload inicial do `index.html` (então o próprio simulador) + `deploy.yml`. Dois commits de teste "Hello→Goodbye" (`62e4dab`, `3b0d36e`).
2. **v2 do simulador**: `confinex_v2.html` vira `index.html` (PR #1); fixes de encoding; depois `923cc65` remove o v2.
3. **Correções de cálculo**: `cabPorCarreta` + frete por carreta; `precoVenda` na sensibilidade (`38be9d0`/`b05507c`); frete por quantidade de carretas (`de497ee`); capital e preço líquido (`8993ec2`); novos defaults (`2c72d2d`).
4. **Persistência**: auto-save de testes (`4f4caf4`); salvar último cálculo (`e560c0d`); recalcular ao abrir (`fbe9404`); alinhar sensibilidade + preservar modalidade de preço (`0479e8a`/`a7c9db8`).
5. **Ecossistema** (`dd01865`): "Fase 0" — nasce a arquitetura atual (bundle `confinex-app.latest.js` + páginas Supabase: Central, BGI, Painel Vivo). `fc9b66c`: Central vira página inicial, Confinex movido para `/confinex.html`, card BGI aponta para `boi-gordo-portfolio`.
6. **Expansões (jul/2026)**: `39686ee` hedge×especulação no Painel + contrato B3 acompanha data de saída; `3462bf1` Boi Balança (bb.html) + filtro de pendências canceladas; `b20df94` `prazo_recebimento` como data prevista; `9fb8c02` Ops dashboard; `69dc3d5` skill promissória (OpenClaw/Juan).
