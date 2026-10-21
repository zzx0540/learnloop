# Architecture and operational limits

Browser (React / TypeScript) → FastAPI routes → ownership dependencies → PostgreSQL + file storage.

Long-running work uses a PostgreSQL queue. A single application process runs one heavy worker, two light workers and scheduled jobs. Queue claiming uses row locks with SKIP LOCKED; active deduplication keys prevent duplicate jobs. Startup recovery assumes a single application instance.

All LLM traffic uses AgentRuntime. Low/medium/high are logical task tiers, not specific products. In the public candidate, empty medium/high model settings inherit the low model; an empty high endpoint inherits the primary endpoint. Structured outputs use Pydantic validation and a repair attempt. Thinking and web-search parameters are opt-in provider capabilities.

Uploads retain their originals. Parsed text becomes versioned Wiki pages, knowledge points and a plan. FSRS schedules reviews. Tests cover permissions, queues, study flow, language exercises, markets and new public-readiness protections; they do not establish educational effectiveness.

## Cost semantics

All course owners, including administrators, use the daily token budget. It is checked before calls and is not a hard currency cap: concurrent calls, retries and provider billing can exceed an estimate. Use provider-side spending limits too.

Prices default to an empty mapping. Configure `llm_pricing` under administrator settings using USD per million tokens: `miss` for uncached input, `hit` for cached input, `out` for output. Unknown prices are stored as -1 in the existing numeric field, never included in positive totals, and surfaced as unknown. No schema migration is introduced. Old zero estimates from an existing installation cannot be retroactively distinguished from free calls. This release candidate is for fresh installations; production upgrade work is outside this preparation.

Costs are recorded estimates, not invoices. Truncated or failed/retried requests may not have complete usage accounting. Configuring prices later does not recompute earlier records.

## Boundaries

- Chinese UI and Asia/Shanghai learning-day boundaries; the English README does not imply an English interface.
- No claim that every OpenAI-compatible endpoint supports every model option.
- English automatic article discovery requires a provider that supports the configured web-search extension. Import reading materials manually otherwise.
- No paid-provider integration test was run for this candidate; no real API keys were accessed.
- The pre-authored demo is for browsing. Model generation, uploads and business writes are intentionally blocked; it is not a simulation of a real model response.
