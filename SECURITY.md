# Security

This candidate has undergone scoped tests and dependency checks, not a comprehensive security audit. Default Compose ports bind to localhost. Before internet deployment, configure HTTPS, `COOKIE_SECURE=true`, a private administrative credential, backups and appropriate access controls.

Do not post credentials, session cookies, learner documents or database dumps in public issues. Before publishing this repository, the maintainer should enable GitHub private vulnerability reporting or provide a private contact channel. No public reporting address is invented in this candidate.

AI providers may receive uploaded document text and user prompts. A local application database does not mean model inference stays local. MinerU and English web-search features can contact external services when configured. The pre-authored demo disables model calls and background workers and uses an internal Docker network; it must remain bound to localhost because its sample credentials are public.

Dependency reports describe known advisories at the time of the check, not a guarantee against vulnerabilities. Never point the test harness at a database containing user data.
