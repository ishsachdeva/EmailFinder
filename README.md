# EmailFinder

EmailFinder is a reusable, local desktop B2B prospecting foundation. It accepts a company-specific YAML brief while keeping the prospecting engine independent of the Qt UI.

## Current scope: Phase 2A

Phase 1 provides the desktop shell, validated Company Brief, SQLite persistence, provider protocols, deterministic mocks, confidence gating, deduplication/suppression, and the mock vertical slice. Phase 2A adds one real, evidence-first path: Brave Search discovery, direct retrieval of small excerpts from public company pages, deterministic identity/hard-exclusion checks, and structured NVIDIA company qualification. No web server is used.

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

Email discovery and verification remain separate and are unused by Phase 2A. NVIDIA receives only stored evidence and cannot establish company existence or mailbox existence. Final ICP scoring is inspectable: configurable weights are applied in Python to NVIDIA's evidence interpretations.

### Why Brave Search

Phase 2A uses exactly one discovery service: the official Brave Search API. It provides a stable authenticated endpoint and result URLs from an independent public index, with monthly credits suitable for a small validation. Search snippets identify candidates only; the system visits the underlying public company pages before qualification. LinkedIn automation and search-engine scraping are not used.

Create a Brave Search API account and subscription token through the official **Brave Search API Dashboard**. Current signup may require selecting a plan/payment method even when monthly credits cover the validation run. Put the token only in local `.env` as `SEARCH_API_KEY`.

For a strictly $0 smoke test, `SEARCH_PROVIDER=WIKIDATA` selects Wikidata's public CC0 query service instead. It requires no account or key, returns structured entity and official-website URLs, and is deliberately limited by `DISCOVERY_LIMIT`. Its coverage and freshness are weaker than a commercial search index.

`SEARCH_PROVIDER=TAVILY` is the preferred Phase 2A discovery path. It builds signal-led queries from the Company Brief, screens a bounded result pool, and treats snippets only as `SEARCH_DISCOVERY`. Configure `TAVILY_API_KEY`; Tavily's free Researcher allowance stops at its credit limit unless the account is explicitly upgraded.

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

For a real Phase 2A company-only run, configure:

```dotenv
RUN_MODE=REAL
COMPANY_BRIEF_PATH=examples/company_brief.appifyu.yaml
SEARCH_PROVIDER=BRAVE
SEARCH_API_KEY=your-local-brave-token
NVIDIA_API_KEY=your-local-nvidia-key
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=your-enabled-model-id
```

Never commit `.env`. Missing credentials fail with a readable configuration error. REAL mode processes companies synchronously (bounded concurrency of one), limits NVIDIA to 36 requests/minute, uses at most two attempts for transient/invalid responses, and reuses previously completed domains.

Run tests with:

```powershell
pytest
```

## Company Brief

`examples/company_brief.example.yaml` describes the seller, ICP, buyers, qualification thresholds, scoring weights, personalization constraints, and run limits. `examples/company_brief.appifyu.yaml` is a test configuration only; application logic contains no AppifyU dependency. Invalid/unknown fields or weights not totaling 100 fail with a readable error.

## Not implemented

There is no person/contact discovery, email lookup or inference, mailbox probing, email verification call, outreach, CRM, analytics, authentication, cloud service, packaging, or polished UI. Phase 2A retrieves ordinary public web pages referenced by discovery results; it does not perform browser automation, prohibited scraping, or LinkedIn automation.

Phase 2B begins only when explicitly authorized. Its boundary is person/buyer discovery and later email stages; none of those capabilities are implemented here.
