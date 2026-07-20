# Conhecimento público do CFAgro / Confinex

Este arquivo deixou de duplicar a memória completa do projeto. O repositório é
público e, portanto, não deve armazenar dados financeiros, nomes de pessoas,
infraestrutura, credenciais ou pendências privadas.

## Fontes públicas canônicas

- `AGENTS.md` — regras de trabalho e ordem de leitura para agentes.
- `CLAUDE.md` — visão técnica atual dos aplicativos e integrações.
- `DESIGN.md` — Design System e convenções visuais.
- `docs/arquitetura.md` — arquitetura, backend, deploy e dívidas técnicas.
- `docs/regras-de-negocio.md` — fórmulas e regras de cálculo.
- `docs/historico.md` — evolução do projeto.

## Conteúdo privado

Contexto de negócio, infraestrutura e pendências ficam exclusivamente em
`docs/privado/`, que é ignorado pelo Git. Segredos permanecem no gerenciador de
senhas e nunca devem ser copiados para este repositório.

> Atenção: remover conteúdo da versão atual não apaga versões antigas do
> histórico Git. Se algum segredo real tiver sido publicado, ele deve ser
> rotacionado; reescrever o histórico é uma ação separada e coordenada.
