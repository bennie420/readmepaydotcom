# TEST_READY: Open-Source Sponsorship Badge Platform Test Suite

## Executive Summary
The independent, opaque-box E2E test suite has been fully implemented across all 4 tiers adhering to the specifications in `ORIGINAL_REQUEST.md`, `PROJECT.md`, and `TEST_INFRA.md`.

- **Total Test Cases**: 167 tests (exceeding the requirement of ≥150 tests).
- **Test Methodology**: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial + Real-World Workload Testing.
- **Strict Compliance**:
  - ZERO `PYTEST_CURRENT_TEST` short-circuiting.
  - ZERO synthetic mock fallback personas or fake dollar figures in production models. All missing records produce authentic 404s or transparent error SVGs.
  - Full Decimal precision verification for 50/50 revenue sharing and CPC deductions.
  - Clean linting status verified via `ruff check tests/` (0 errors).

---

## Test Inventory & Tier Breakdown

| Tier | File Path | Focus Area | Planned | Implemented |
|---|---|---|:---:|:---:|
| **Tier 1** | `tests/e2e/test_tier1_features.py` | Feature Isolation (F1 - F13) | ≥65 | **68** |
| **Tier 2** | `tests/e2e/test_tier2_boundaries.py` | Boundary Values & Edge Conditions | ≥65 | **75** |
| **Tier 3** | `tests/e2e/test_tier3_combinations.py` | Pairwise & Combinatorial Interactions | ≥15 | **17** |
| **Tier 4** | `tests/e2e/test_tier4_scenarios.py` | Full Real-World Application Lifecycles | ≥5 | **7** |
| **Total** | | | **≥150** | **167** |

---

## Detailed Coverage Matrix

### Tier 1: Feature Isolation (68 tests)
- **F1 (Dynamic SVG Badge Endpoint)**: 6 tests (`GET /badge/{owner}/{repo}.svg`, HTTP 200, `image/svg+xml`, viewBox `0 0 500 110`, repo metadata, matched sponsor copy, caching headers).
- **F2 (Real GitHub Metadata Query & Cache)**: 5 tests (Stars, language, passing CI/CD status, TTL caching idempotency, rate-limit resilience).
- **F3 (Transparent Error Handling)**: 5 tests (Honest 404/error SVG, zero synthetic fallback personas, malformed path handling, SVG MIME preservation, XML validity).
- **F4 (Language-Targeted Ad Matching)**: 6 tests (Python targeting, TypeScript targeting, Rust targeting, general fallback for unmatched languages, inactive ad bypassing, case-insensitive language matching).
- **F5 (Click-Through Tracking & 302 Redirect)**: 6 tests (`GET /click/{ad_id}/{repo_id}`, 302 redirect, click persistence, SHA-256 client hash, CPC budget deduction, 0 budget handling, `/click/active/{repo_id}` resolution).
- **F6 (Embedded SVG Hyperlink `<a>`)**: 5 tests (`<a ...>` tag presence, `target="_blank"`, `href` to click redirect, sponsor card wrapper, XML escaping).
- **F7 (Impression Tracking & Dedup)**: 5 tests (Badge view impression record, 64-char SHA-256 client hash, hourly sliding-window deduplication, distinct client records, foreign key linkage).
- **F8 (SQLAlchemy 2.0 Persistence Engine)**: 5 tests (Repository CRUD, Ad Decimal precision CRUD, Impression foreign key integrity, Click foreign key integrity, SQLite foreign key PRAGMA enforcement).
- **F9 (DB Auto-Init & Migrations)**: 5 tests (Table metadata existence, indexes on query columns, idempotent initialization, transaction rollback safety, timestamp auto-population).
- **F10 (Real GitHub Repo Seeding Script)**: 5 tests (Unclaimed repo creation, valid stars and languages, duplicate avoidance, sample ad creation, positive budgets/CPCs).
- **F11 (Maintainer Repo Claiming)**: 5 tests (`POST /maintainers/claim` success, maintainer handle storage, conflict rejection for claimed repos, 404 for unindexed repos, payout address persistence).
- **F12 (Markdown/HTML Snippet Generation)**: 5 tests (Markdown snippet, HTML snippet, RST snippet, valid click/badge URLs, 404 for unindexed repos).
- **F13 (50/50 Revenue Split Calculation)**: 5 tests (Exact 50/50 division, Decimal precision without floating drift, click and impression aggregation, zero click zero revenue, 404 for missing repos).

### Tier 2: Boundary & Corner Cases (75 tests)
- Repositories with dots, dashes, underscores, and max 100-character names.
- Zero stars and 1,500,000+ stars SVG formatting.
- XML meta-character escaping (`<`, `>`, `&`, quotes) preventing XML/XSS injection.
- URL-encoded characters and unicode repo names.
- Missing path segments and missing `.svg` extension handling.
- Null or missing CI/CD status rendering.
- Empty or whitespace-only primary languages.
- Special language symbols (`C++`, `C#`, `Objective-C`).
- Zero budget ads, CPC equal to exact budget, CPC exceeding budget (preventing negative balances).
- Fractional penny CPCs (e.g., $0.025) and exact Decimal arithmetic.
- Negative IDs, non-numeric IDs, and huge 64-bit integer IDs.
- Preserving complex query parameters and hash fragments in 302 redirects.
- Missing IP, missing User-Agent, and 4096-character User-Agent header stability.
- GitHub Camo proxy `X-Forwarded-For` and `Via` header parsing.
- 59-minute vs 61-minute sliding window deduplication boundaries.
- Ad headline (255 chars) and CTA (100 chars) length boundaries.
- SQLite foreign key constraints, PRAGMAs, and rollback isolation.
- Empty string maintainer handles and whitespace-only handle validation.
- Revenue splits for $0.01 (odd cents), $0.03, $1,000,000.00, and 50 small clicks accumulation.

### Tier 3: Pairwise Combinations (17 tests)
- Ad rotation upon budget depletion (Primary ad depleted -> next badge serves secondary ad).
- Language matching cascade to general campaign when targeted campaign runs out of budget.
- Multi-campaign 50/50 revenue ledger consolidation across multiple sponsors.
- Full SVG hyperlink navigation loop (Render badge -> extract `<a>` tag -> follow href -> 302 redirect to sponsor).
- Multi-client concurrent impression deduplication (2 distinct clients -> 2 distinct hashes and records).
- Maintainer onboarding, repo claiming, and snippet generation lifecycle.
- Case-insensitivity matrix (`PYTHON`, `python`, `Python`, `PyThOn`).
- Shared sponsor campaign budget depletion across multiple distinct repositories.
- Sequential click budget deduction integrity without overdraw.
- Multi-repository revenue isolation (Repo 1 clicks do not leak into Repo 2).
- GitHub Camo proxy IP audit hashing without collisions.
- Reactivating exhausted sponsor campaigns and re-entering rotation.
- Unclaimed repository revenue accrual and maintainer claim transition.
- Dynamic `/click/active/{repo_id}` resolution.
- Simultaneous coexistence of repo name, stars, language, CI/CD status, and sponsor copy in SVG XML.
- Zero budget ad exclusion from matching even if target language matches.
- Workflow loop: View badge (1 imp) -> Click ad (1 click) -> View badge (0 new imp).

### Tier 4: Real-World Lifecycle Scenarios (7 scenarios)
1. **Scenario 1: Complete Maintainer Lifecycle**:
   Seed repo -> Request badge -> Claim repo -> Fetch snippet -> User views badge (impression) -> User clicks badge (302 redirect) -> Maintainer queries revenue report (exact 50/50 split).
2. **Scenario 2: Sponsor Campaign Lifecycle & Exhaustion Cascade**:
   Targeted campaign creation -> Matched on repo -> Clicks deplete budget -> Budget reaches 0.00 -> Badge rotates seamlessly to general sponsor -> Clicks route to general sponsor.
3. **Scenario 3: Cache Resiliency & Honest 404 Resiliency**:
   Initial fetch -> Max-age=3600 header validation -> Cached repeat fetch -> Non-existent repo returns 404/honest error SVG with strictly zero synthetic "Vance" or fake data.
4. **Scenario 4: Click Fraud Audit Trail & Impression Deduplication**:
   Rapid 10-view burst from Client A (deduped to 1 record) -> Client B views badge (2nd record with distinct SHA-256 hash) -> Both click -> Complete fraud audit trail validated.
5. **Scenario 5: Multi-Language Inventory Matrix**:
   Python, TypeScript, Rust, and Go repositories matched to respective specialized sponsors; unknown language repository matched to general fallback sponsor.
6. **Scenario 6: Multi-Tenant Revenue Payout & Ledger Reconciliation**:
   3 maintainers with different traffic volumes and CPCs; mathematical reconciliation of gross revenue, individual maintainer payouts, and platform cut.
7. **Scenario 7: Adversarial Traffic & Escaping Integrity**:
   XSS strings, format injection, and special characters tested against SVG generator; verification that Jinja2 autoescaping prevents injection and compiles valid SVG XML.

---

## Harness & Fixtures Architecture (`tests/conftest.py`)
- **FastAPI ASGI Harness**: Synchronous `TestClient` and asynchronous `httpx.AsyncClient` with `ASGITransport`.
- **Database Isolation**: In-memory SQLite (`sqlite:///:memory:`) using `StaticPool` with `PRAGMA foreign_keys=ON;` enforced on every connection. Tables created and torn down per test.
- **Factory Helpers**:
  - `create_test_repository(...)`: Seeds repositories with custom stars, language, claiming status.
  - `create_test_ad(...)`: Seeds ads with custom budgets, CPCs, and targeting.
  - `create_test_impression(...)`: Logs audit-hashed impressions.
  - `create_test_click(...)`: Logs clicks with cost and audit hash.
  - `compute_client_hash(...)`: Computes deterministic SHA-256 audit hashes.
  - `assert_valid_svg_xml(...)`: Asserts well-formed XML and `<svg>` root element.
- **Progressive Testability**: Fixtures gracefully skip if application modules are in intermediate implementation, and execute 100% against live application code once modules exist.

---

## Runner Commands

### Run All Tests
```powershell
pytest tests/
```

### Run by Specific Tier
```powershell
# Tier 1: Feature Isolation
pytest tests/e2e/test_tier1_features.py

# Tier 2: Boundary & Corner Cases
pytest tests/e2e/test_tier2_boundaries.py

# Tier 3: Combinatorial Interactions
pytest tests/e2e/test_tier3_combinations.py

# Tier 4: Real-World Scenarios
pytest tests/e2e/test_tier4_scenarios.py
```

### Run with Verbose Output & Summary
```powershell
pytest tests/ -v --tb=short
```

### Linting & Code Quality Check
```powershell
ruff check tests/
```
