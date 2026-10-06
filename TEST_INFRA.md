# E2E Test Infra: Open-Source Sponsorship Badge Platform

## Test Philosophy
- Opaque-box, requirement-driven. No dependency on implementation design.
- Methodology: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial + Real-World Workload Testing.
- Zero `PYTEST_CURRENT_TEST` bypasses: tests run against live application code without synthetic short-circuits.
- Zero synthetic mock fallback data: all entities are authentic, and missing records produce genuine 404s.

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| F1 | Dynamic SVG Badge Rendering | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| F2 | Real GitHub Metadata Query & Cache | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| F3 | Transparent Error Handling (404/Error SVG)| ORIGINAL_REQUEST §R1, Rules | 5 | 5 | ✓ |
| F4 | Language-Targeted Ad Matching | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| F5 | Click-Through Tracking & 302 Redirect | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| F6 | Embedded SVG Hyperlink (`<a>`) | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| F7 | Impression Tracking & SHA-256 Dedup | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| F8 | SQLAlchemy 2.0 Persistence Engine | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| F9 | DB Auto-Init & Migrations | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| F10 | Real GitHub Repo Seeding Script | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| F11 | Maintainer Repo Claiming | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |
| F12 | Markdown/HTML Snippet Generation | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |
| F13 | 50/50 Revenue Split Calculation | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |

## Test Architecture
- **Test Runner**: Pytest with `asyncio_mode = auto` and FastAPI `TestClient` / `ASGITransport`.
- **Test Directory Layout**:
  - `tests/e2e/test_tier1_features.py`: Happy-path feature coverage (>=5 tests per feature).
  - `tests/e2e/test_tier2_boundaries.py`: Boundary conditions, empty strings, invalid characters, edge cases.
  - `tests/e2e/test_tier3_combinations.py`: Pairwise cross-feature interactions (ad rotation + language + click + revenue).
  - `tests/e2e/test_tier4_scenarios.py`: End-to-end full lifecycle maintainer & sponsor workflows.
- **Pass/Fail Semantics**: All tests must assert exit code 0, status 200/302/404 as specified, valid XML parsing, and exact decimal math.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Full Maintainer Lifecycle: Seed repo -> Request badge -> Claim repo -> Verify snippet -> Track clicks -> Verify 50/50 payout report | F1, F2, F5, F10, F11, F12, F13 | High |
| 2 | Sponsor Lifecycle: Create campaign -> Language targeting -> Match on Python repo -> Deplete budget -> Auto-deactivate -> Fallback to general sponsor | F4, F5, F8, F13 | High |
| 3 | Cache & GitHub Rate-Limit Resiliency: Initial fetch -> Repeat fetch with ETag/304 -> GitHub 404 honest error handling -> Verify zero mock personas | F1, F2, F3, F7 | Medium |
| 4 | Click Audit & Impression Deduplication: Rapid sequential impressions/clicks within window -> Verify SHA-256 hash -> Verify deduplication | F5, F7, F8 | Medium |
| 5 | Multi-Language Inventory Matrix: Python, TypeScript, Rust, Go repos -> Confirm correct sponsor matched for each language | F1, F4, F8 | Medium |

## Coverage Thresholds
- Tier 1: ≥5 per feature (65 tests)
- Tier 2: ≥5 per feature where boundaries exist (65 tests)
- Tier 3: Pairwise coverage of major feature interactions (≥15 tests)
- Tier 4: ≥5 realistic end-to-end application scenarios
- **Total planned test cases**: ≥150 test cases.
