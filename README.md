# EmailFinder

EmailFinder is the Phase 1 foundation for a reusable, local desktop B2B prospecting application. It accepts a company-specific YAML brief while keeping the prospecting engine independent of the Qt UI.

## Current scope

Phase 1 provides a PySide6 desktop shell, validated Pydantic Company Brief, SQLite/SQLAlchemy persistence, pipeline states, provider protocols, deterministic mock providers, confidence gating, deduplication/suppression helpers, and a five-candidate mock vertical slice. No web server is used.

The mock run contains one verified acceptance, one high-confidence acceptance, one rejected ICP, one rejected buyer, and one risky address. Every fixture evidence record and hypothesis is visibly marked `MOCK FIXTURE`.

## Architecture

```text
src/emailfinder/
  app/          application entry point
  config/       YAML/Pydantic Company Brief
  domain/       states, verification result, errors
  providers/    protocols and deterministic mocks
  persistence/  SQLite/SQLAlchemy schema and helpers
  services/     confidence gate and orchestration
  ui/           PySide6 Qt Widgets shell
examples/       editable example Company Brief
tests/          offline Phase 1 tests
```

Email discovery and verification are separate provider boundaries. The future NVIDIA boundary is configuration-only in this phase; reasoning cannot establish mailbox existence.

## Setup and run

Prerequisite: Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
emailfinder
```

Click **Build Today's List** to run only the deterministic fixtures. By default SQLite data is stored at `emailfinder.db` in the repository. Set `DATABASE_PATH` in `.env` to change it.

Run tests with:

```powershell
pytest
```

## Company Brief

`examples/company_brief.example.yaml` describes the seller, ICP, buyers, qualification thresholds, personalization constraints, and run limits. Invalid or unknown fields fail with a readable validation error. Replace its example values for another company; the application contains no AppifyU dependency.

## Not implemented

There is no real discovery, scraping, enrichment, contact/email lookup, NVIDIA call, mailbox probing or verification service, outreach, CRM, analytics, authentication, cloud service, packaging, or polished UI.

Phase 2 begins only when explicitly authorized: implement selected real provider adapters behind the existing interfaces, with evidence provenance, rate/error handling, and credentials. Real prospect discovery or API usage is the exact boundary and is intentionally absent here.

