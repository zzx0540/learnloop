# LearnLoop

**Turn your own study materials into a daily plan and spaced review.**

[简体中文](README.md) · [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md)

![A pre-authored SQL study plan](docs/images/plan.png)

LearnLoop connects source documents, versioned Wiki pages, planning, retrieval practice, quizzes and FSRS review. It is a self-hosted, multi-user application. This public-release candidate is awaiting author review; the UI is Chinese and learning-day boundaries use Asia/Shanghai.

## Try the pre-authored demo

Docker and Compose v2 are required. The initial build downloads images and dependencies.

```bash
docker compose -f docker-compose.demo.yml up -d --build
```

Open **http://localhost:8088**. Sign in as `demo` with `learnloop-demo-only`.

The demo contains original, fictional SQL lessons, a three-day plan, Wiki pages, a concept graph and review cards. It is read-only except for login sessions. It disables model calls and background workers, uses its own database and volumes, and binds to localhost. Its content is pre-authored, not evidence of model output quality. [Watch the local browser walkthrough](docs/demo-walkthrough.webm) (WebM, Chinese captions, no narration).

Stop it without deleting data: `docker compose -f docker-compose.demo.yml stop`.

## Use your own materials

Run `python tools/setup.py` with Python 3.10+ to generate `.env` with random local credentials. It will not overwrite an existing file or print passwords. Edit it locally and set your provider's Chat Completions base URL (`DEEPSEEK_BASE_URL`), key (`DEEPSEEK_API_KEY`) and actual model ID (`MODEL_LOW`). The names retain historical compatibility; the endpoint may be another compatible provider.

One model is enough to start an ordinary knowledge course. Empty medium/high model IDs inherit the low model; an empty high endpoint uses the primary endpoint. Provider-specific thinking and web-search extensions are disabled by default and require explicit opt-in after checking provider support.

```bash
docker compose up -d --build
```

Open **http://localhost:8080** and sign in with your generated administrator credentials. Create a knowledge course and upload the original example in `examples/sql-basics.md`. After ingestion, discuss your goal with the planner and generate a plan. Markdown/TXT needs no external parser; optional MinerU handles additional document formats. English automatic article discovery requires a compatible search-enabled provider; import material manually otherwise.

## Features

- Original uploads, document parsing, versioned Wiki pages and resumable ingestion.
- Plans, knowledge points, concept graphs and study materials.
- Quizzes, explanations, weakness tracking and FSRS review.
- English vocabulary, reading, writing and assessment flows.
- Invitations, course ownership checks, a course marketplace and usage records.

These features do not establish measured educational efficacy. Generated teaching content and grading need human review.

## Costs and operation

AI services may charge for usage and receive your documents and prompts. All course owners, including administrators, share the configured daily per-course token-budget rule. This is not a hard monetary cap; use provider-side limits too.

Prices start empty. Unknown costs are shown as unknown rather than free. Configure USD-per-million-token prices in administrator settings. Estimates may omit usage from failed/retried calls and do not replace provider invoices; earlier records are not repriced automatically.

Back up both the PostgreSQL and uploaded-file volumes. The default port binds to localhost. Configure HTTPS, secure cookies and access controls before remote access. Run a single backend process: worker recovery and scheduling assume one instance.

## Review status

The Docker image was built locally; a fresh PostgreSQL 16 demo and 112 same-image backend tests passed, along with desktop/mobile browser checks. One bounded live DeepSeek Flash run covered original SQL material ingestion, planning, quiz generation, grading and review. See [runtime acceptance](docs/local-runtime-acceptance.md) and [earlier verification](docs/verification.md). **GitHub-hosted Actions have not run.** This small live sample does not validate every provider, document format or language-learning flow.

## License

The project license has not been selected; this candidate does not grant an open-source license. Existing PDF/EPUB parsers include AGPL dependencies. Review [third-party licensing](docs/third-party-licenses.md) before public distribution. The candidate excludes production secrets, user data, operational history and the original Git history.
