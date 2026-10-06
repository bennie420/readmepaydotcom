# Project: Open-Source Sponsorship & Dynamic README Badge Ad Platform

## Architecture
- **Language & Framework**: Python 3.12, FastAPI, Uvicorn (defaulting to port 8080 or configurable `PORT`).
- **Database & Storage**: SQLAlchemy 2.0 with SQLite (`sqlite:///./badge_platform.db` / `sqlite+aiosqlite:///./badge_platform.db`), WAL mode enabled (`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`), and foreign keys enforced (`PRAGMA foreign_keys=ON;`).
- **Template & Rendering**: Jinja2 with `select_autoescape(['xml', 'svg', 'html'])` compiling valid SVG XML with responsive `viewBox="0 0 500 110"`.
- **External Integration**: Async HTTPX client for real GitHub REST API v3 queries (`/repos/{owner}/{repo}` and `/actions/runs`), supported by 1-hour TTL in-memory caching and optional `GITHUB_TOKEN`.
- **Security & Auditability**: One-way SHA-256 client audit hashing (IP + User-Agent + Salt), zero synthetic/mock fallback personas or statistics in production models, zero `PYTEST_CURRENT_TEST` bypasses.
- **Financial Ledger**: Exact `Decimal` mathematics for 50/50 maintainer revenue share and sponsor budget depletion.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Dynamic SVG Badge Endpoint | `GET /badge/{owner}/{repo}.svg` compiling responsive SVG via Jinja2 with inline styling | M2 | ORIGINAL_REQUEST §R1 |
| F2 | Real GitHub Metadata Query | Live queries for stars, language, CI/CD status with `Cache-Control: public, max-age=3600` | M2 | ORIGINAL_REQUEST §R1 |
| F3 | Honest Error Handling | Non-existent repos return authentic 404 or error SVG; zero synthetic fallback personas | M2 | ORIGINAL_REQUEST §R1, Rules |
| F4 | Language Matching & Fallback | Match sponsor campaigns by repo primary language, falling back to general sponsor | M2 | ORIGINAL_REQUEST §R1 |
| F5 | Click-Through Redirect Endpoint | `GET /click/{ad_id}/{repo_id}` logging click, deducting budget, and 302 redirecting to sponsor URL | M3 | ORIGINAL_REQUEST §R2 |
| F6 | Embedded SVG Hyperlink | Standard SVG `<a href="..." target="_blank">` element surrounding ad card in badge | M2 | ORIGINAL_REQUEST §R2 |
| F7 | Impression Tracking & Dedup | Hourly sliding-window deduplication using SHA-256 client audit hash | M3 | ORIGINAL_REQUEST §R2 |
| F8 | SQLAlchemy 2.0 Data Models | Repositories, Ads, Impressions, and Clicks with strict schema and indexes | M1 | ORIGINAL_REQUEST §R3 |
| F9 | DB Table Init & Migrations | Table auto-creation on startup and migration support | M1 | ORIGINAL_REQUEST §R3 |
| F10 | Top Repositories Seed Script | Discover and persist real top open-source repos using real GitHub Search API | M1 | ORIGINAL_REQUEST §R3 |
| F11 | Maintainer Onboarding & Claiming | Claim repo endpoints with maintainer verification and conflict handling | M4 | ORIGINAL_REQUEST §R4 |
| F12 | Snippet Generator | Copy-pasteable GitHub Markdown/HTML badge snippet generation | M4 | ORIGINAL_REQUEST §R4 |
| F13 | 50/50 Revenue Share Reporting | Financial reporting endpoints reflecting exact 50/50 maintainer/platform split | M4 | ORIGINAL_REQUEST §R4 |
| F14 | Automated Test Rig | Automated test suite verifying XML validity, ad matching, redirects, 404 handling | M5 | ORIGINAL_REQUEST §R5 |
| F15 | Opaque-Box E2E Test Suite | Comprehensive 4-tier requirement-driven test suite publishing `TEST_READY.md` | M-E2E | Project Pattern |
| F16 | Adversarial Hardening (Tier 5) | White-box stress-testing, edge cases, and security boundary verification | M5 | Project Pattern |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Data Models & Seeding Engine | SQLAlchemy 2.0 schema (`Repository`, `Ad`, `Impression`, `Click`), WAL mode, client hash, real GitHub Search API seed script | none | DONE |
| M2 | Dynamic SVG Badge Engine | FastAPI `/badge/{owner}/{repo}.svg`, Jinja2 SVG template, live GitHub client, caching headers, language matching cascade, honest 404 | M1 | DONE |
| M3 | Click Tracking & Analytics Pipeline | FastAPI `/click/{ad_id}/{repo_id}`, 302 redirect, budget deduction, impression logging & deduplication, `/click/active/{repo_id}` | M1, M2 | DONE |
| M4 | Maintainer Onboarding & Revenue Sharing | Claiming endpoints, snippet generator, 50/50 revenue ledger and reporting endpoints | M1, M2, M3 | DONE |
| M-E2E | E2E Testing Track | Independent requirement-driven test suite (Tiers 1-4), harness, assertions, publishing `TEST_READY.md` | none (runs in parallel) | DONE |
| M5 | Final Milestone: E2E Test Pass & Hardening | Phase 1: 100% pass of E2E test suite (Tiers 1-4); Phase 2: Adversarial coverage hardening (Tier 5) | M1, M2, M3, M4, M-E2E | DONE |

## Interface Contracts

### M1 ↔ M2 (Data Models ↔ Badge Engine)
- `get_or_fetch_repository(db: Session, owner: str, name: str) -> Optional[Repository]`
  - Returns `Repository` model instance or `None` if not found on GitHub.
- `match_ad_for_repository(db: Session, primary_language: Optional[str]) -> Optional[Ad]`
  - Matches active ad by language (case-insensitive) or falls back to general ad (`target_language IS NULL` or `'general'`) with `remaining_budget > 0`.
- `build_badge_svg(repo_name: str, stars: int, language: Optional[str], ci_status: Optional[str], ad: Optional[Ad], click_url: str) -> str`
  - Returns valid XML SVG string.

### M1/M2 ↔ M3 (Badge & Ad Data ↔ Click Tracking)
- `record_click(db: Session, ad_id: int, repo_id: int, client_ip: str, user_agent: str, referer: Optional[str]) -> Tuple[str, Click]`
  - Validates `ad_id` and `repo_id`, verifies budget, records `Click` with SHA-256 `client_hash`, deducts CPC from `ad.remaining_budget`, credits maintainer, and returns `(ad.click_url, click_instance)`.
- `record_impression(db: Session, ad_id: int, repo_id: int, client_ip: str, user_agent: str, is_camo: bool) -> Optional[Impression]`
  - Hourly sliding window deduplication by `(repo_id, ad_id, client_hash)`.

### M1/M3 ↔ M4 (Storage & Tracking ↔ Onboarding & Revenue)
- `claim_repository(db: Session, owner: str, name: str, maintainer_handle: str, payout_address: Optional[str]) -> Repository`
  - Validates ownership, ensures unclaimed, sets `claimed=True`.
- `generate_markdown_snippet(base_url: str, owner: str, name: str, repo_id: int) -> Dict[str, str]`
  - Returns markdown, html, and rst snippets linking to `/click/active/{repo_id}`.
- `calculate_repository_revenue(db: Session, repo_id: int) -> RevenueReport`
  - Returns gross revenue, maintainer earnings (50.0%), platform cut (50.0%), total clicks, total impressions.

## Code Layout
```
c:/Users/ben/Documents/antigravity/hopeful-bardeen/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app factory, routes inclusion, lifespan
│   ├── config.py            # Settings (PORT, DB_URL, GITHUB_TOKEN, SALT)
│   ├── database.py          # SQLAlchemy engine, sessionmaker, WAL event listeners
│   ├── models/
│   │   ├── __init__.py
│   │   ├── repository.py    # Repository ORM model
│   │   ├── ad.py            # Ad ORM model
│   │   └── analytics.py     # Impression & Click ORM models
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── repo.py
│   │   ├── ad.py
│   │   └── revenue.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── github_service.py # GitHub REST API v3 async client + TTL cache
│   │   ├── badge_service.py  # Jinja2 SVG compiler & XML validator
│   │   ├── matching_service.py # Language matching & fallback engine
│   │   ├── tracking_service.py # Impression dedup, click logging, SHA-256 hash
│   │   └── revenue_service.py  # 50/50 revenue math & reporting
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── badge.py         # GET /badge/{owner}/{repo}.svg
│   │   ├── click.py         # GET /click/{ad_id}/{repo_id} & /click/active/{repo_id}
│   │   ├── maintainers.py   # Claiming & snippet generation
│   │   └── revenue.py       # Revenue share reporting
│   └── templates/
│       ├── badge.svg.j2     # Dynamic SVG template (viewBox 0 0 500 110)
│       └── error.svg.j2     # Transparent error SVG template
├── scripts/
│   ├── seed_top_repos.py    # Real GitHub Search API repository seeder
│   ├── seed_sample_ads.py   # Tech sponsor campaign seeder
│   └── run_verification_rig.py # Standalone E2E verification rig
├── tests/
│   ├── conftest.py
│   ├── test_models.py
│   ├── test_badge_svg.py
│   ├── test_matching.py
│   ├── test_click_tracking.py
│   ├── test_onboarding.py
│   ├── test_revenue.py
│   ├── test_github_service.py
│   └── e2e/
│       ├── test_tier1_features.py
│       ├── test_tier2_boundaries.py
│       ├── test_tier3_combinations.py
│       └── test_tier4_scenarios.py
├── PROJECT.md
├── pytest.ini
└── README.md
```
