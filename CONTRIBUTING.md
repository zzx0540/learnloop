# Contributing

This is a public-release candidate under review. The interface is Chinese; the English README describes setup and scope. Original project code is AGPL-3.0-only. By submitting a contribution you agree to license it under the same terms and confirm you have the necessary rights. Retain third-party notices and identify any newly introduced licensed materials.

## Local development

Use Python 3.12, Node.js 22, and PostgreSQL 16. Copy `.env.example` using `python tools/setup.py`; never include local secrets in issues or commits. Backend dependencies are constrained to the reviewed versions in `backend/constraints.txt`; frontend uses `npm ci` and its committed lockfile.

Run tests only against a disposable `learnloop_test` database owned by a dedicated `learnloop_test` role, on localhost or the isolated `pg-test` service. Set `TEST_DATABASE_URL` and `LEARNLOOP_ALLOW_TEST_RESET=1`. The test harness deliberately recreates application tables, and binds all service sessions to that test database before importing the application. Do not use a role with access to any other application database. Run `python -m pytest -q` from `backend/`; run `npm ci && npm run build` from `frontend/`.

CI creates its own PostgreSQL service, validates migrations, runs backend tests and builds the frontend. A separate job builds the isolated Docker demo. Workflow files are included; a green GitHub run is required before tagging a release.

## Changes

Keep model calls in `AgentRuntime`, verify course ownership for user data, and preserve task deduplication and original documents. Use Alembic for schema changes. The application currently supports a single backend process; do not add replicas without redesigning worker recovery and scheduling.

For a fix, describe the user-visible problem, add a focused regression test where useful, and document migration or compatibility implications. Never attach real user documents, tokens or database dumps. Use the original examples in `examples/`.

After source changes, regenerate local legal artifacts with `python tools/package_source.py . .legal`. Docker builds do this automatically. The download must describe the code actually running, and must never include secrets or learner files.
