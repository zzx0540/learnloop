# Public candidate worklog

## 2026-10-20 [codex] Independent public-release preparation

Work is confined to this sanitized copy. The original repository and running server are unchanged. Added a local-only pre-authored demo, deterministic setup, model-tier fallback and explicit capabilities, unknown-cost reporting, administrator budget enforcement, guarded isolated tests, CI, bilingual documentation and synthetic demo assets. Updated frontend packages for reported advisories and fixed portable test dates/image paths. Original production history and user evidence are excluded. The final review report records actual checks and remaining limitations. No remote push, release tag or production deployment is authorized by this worklog.

## 2026-10-20 [codex] Local container and live-provider acceptance

Installed local WSL/Docker with system elevation as needed; built the candidate and passed 112 PostgreSQL16 same-image tests. Fixed demo port reachability using a localhost-only Nginx gateway; the application remains on the internal network. A bounded real DeepSeek Flash run covered ingestion, planning, quiz generation, grading and simulated-due review. Removed the temporary provider key after use. Evidence: docs/local-runtime-acceptance.md. Production and original repository unchanged; GitHub-hosted CI and licensing review remain pending.

## 2026-10-20 [codex] Private GitHub preparation

Prepared an AGPL-3.0-only proposal without granting a project license, added CI time limits and same-branch cancellation, and added sanitized issue/PR templates. The authorized next action is a personal private repository and GitHub-hosted CI; visibility must remain private until the author explicitly confirms publication.

Private staging repository created under the confirmed personal account: https://github.com/zzx0540/learnloop. Uploaded sanitized history only. Actions are enabled for main; no model credentials are stored in the repository or supplied to CI. Repository visibility remains PRIVATE.

## 2026-10-20 [codex] Adopt AGPL-3.0-only and prepare authorized publication

Author explicitly adopted AGPL-3.0-only and authorized making zzx0540/learnloop public after final checks. Added full license, project notice, package metadata, preserved dependency/font notices and public build-time source/notice endpoints. Only the independent candidate is affected; production and original source remain unchanged. Final CI and repository-visibility evidence are recorded outside the source tree to keep the tested commit unchanged.
