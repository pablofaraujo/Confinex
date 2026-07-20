# CFAgro / Confinex

Ecossistema de apps web para gestão de confinamento e giro de gado (marca **CFAgro**, Pablo Ferreira).

🌐 **No ar:** https://pablofaraujo.github.io/Confinex/
📦 **Repositório:** github.com/pablofaraujo/Confinex — **público**

> ⚠️ Repositório público: nunca commitar dados financeiros, nomes de sócios, infraestrutura ou chaves. Isso fica em `docs/privado/` (protegido pelo `.gitignore`).

---

## Os apps

Todos são single-file (um arquivo por app), sem framework de build. Visual e infra compartilhados pelo Design System.

| Página | App | O que faz |
|---|---|---|
| `index.html` | **Visão Geral** | Home/dashboard: KPIs, exposição, estoque, pendências, caixa |
| `confinex.html` | **Confinex** | Simulador de compra/confinamento/revenda (React) |
| `fazenda-ametista.html` | **Fazenda Ametista** | Entradas, saídas e estoque do rebanho próprio |
| `confinamento.html`, `confinados.html` | **Confinamentos** | Operação por confinamento e visão consolidada dos animais confinados |
| `bb.html` | **Boi Balança** | Giro rápido balança → gancho |
| `bgi.html` | **BGI** | Posições de hedge na B3 |
| `abate.html` | **Abate** | Cabeçalho de abate e romaneio animal a animal |
| `ocr-pesagem.html` | **OCR Pesagem** | Lê a foto do caderno de pesagem, você confere, e alimenta o sistema |
| `painel-boi-gordo.html` | **Painel Boi Gordo** | Indicadores, curva futura e contexto de mercado |
| `ops.html` | **Ops / Agentes** | Heartbeats dos agentes + Ponte VPS (fila de missões) |
| `parcerias.html`, `parceria-ricardo.html`, `parceria-xande.html` | **Parcerias** | Acompanhamento das parcerias |
| `painel.html`, `central.html` | *(legadas)* | Redirecionam para `index.html` |

## Como está montado

- **Frontend:** HTML/JS puro, sem build. Cada página carrega o Design System (`design/tokens.css` + `design/components.css`), o núcleo (`js/cfagro-core.js`) e a navegação (`js/cfagro-shell.js`).
- **Backend:** Supabase (Postgres + login por email/senha, RLS protege os dados). Usado pela maior parte dos módulos operacionais, incluindo Visão Geral, Boi Balança, BGI, confinamentos, fazenda, abate, parcerias, OCR Pesagem e Ops.
- **Exceção:** a **Confinex** usa Google Sheets + localStorage (não o Supabase) e roda em React.
- **Agentes/automação:** um agente na VPS ("Juan") executa tarefas e grava no banco. O app **Ops** deposita missões na fila (Ponte VPS) que o agente consome. Detalhes de infra ficam nos docs privados.

## Publicar uma mudança (deploy)

O site publica **automaticamente** a partir do GitHub:

1. Alterou um arquivo → **commit + push na branch `main`**.
2. O GitHub Actions (`deploy.yml`) publica o repositório inteiro no GitHub Pages.
3. Em ~1 min a mudança está no ar em `pablofaraujo.github.io/Confinex/`.

**Importante:**
- Não existe etapa de build — o que está commitado é o que vai pro ar.
- Tudo que estiver no repositório fica **público**. Confira o `.gitignore` antes de subir.
- Editar arquivo local (na pasta do Google Drive, por ex.) **não publica nada** — só o push no GitHub publica.

## Convenções

- Tudo em **pt-BR**: código, variáveis, interface, commits.
- **Design System primeiro:** antes de criar tela/componente/estilo, verificar se já existe em `DESIGN.md` / `design/`. Se não existir, criar no DS (nunca na página) e documentar. Nenhum layout novo nasce fora do DS.
- Constantes de domínio: **1 @ = 15 kg**; **1 contrato BGI = 330 @**; RC padrão 50–53%.
- Toda página-satélite tem o botão flutuante **⌂ Central**.

## Documentação

| Arquivo | Conteúdo |
|---|---|
| `CLAUDE.md` | Guia técnico para o assistente de código |
| `DESIGN.md` | Design System (fonte visual única) |
| `docs/arquitetura.md` | Apps, backend, deploy, dívidas técnicas |
| `docs/regras-de-negocio.md` | Fórmulas e regras de cálculo |
| `docs/historico.md` | Evolução do projeto |
| `docs/privado/` | Negócio, parcerias, infra, pendências — **não versionado** |

## Status de features

| Feature | Situação |
|---|---|
| Visão Geral, Boi Balança, BGI, Ops, Parcerias | No ar |
| Confinex (simulador) | No ar |
| OCR Pesagem — tela de conferência | No ar; integrada ao Design System |
| OCR Pesagem — botão 📷 dentro da Confinex | Pendente; não aparece nos bundles atualmente versionados |
| OCR Pesagem — leitura + gravação no VPS/Juan | Em implementação (missão na Ponte VPS) |
| Reconciliação caderno × Datamars (Fase 2) | Planejada |
