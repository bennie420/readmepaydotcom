# Original User Request

## 2026-10-02T20:24:30Z

Build an end-to-end open-source sponsorship and dynamic README badge ad platform in Python with FastAPI, compiling on-the-fly SVGs, tracking impressions and clicks, matching advertisers by repo language tags, and facilitating revenue sharing for open-source maintainers.

Working directory: c:/Users/ben/Documents/antigravity/hopeful-bardeen
Integrity mode: development

## Requirements

### R1. Dynamic SVG Badge Rendering & Language Matching
- Implement high-concurrency FastAPI endpoint `/badge/{owner}/{repo}.svg` compiling optimized SVG strings via Jinja2 with inline styling and responsive dimensions.
- Query real live GitHub API metadata (stars count, primary language, CI/CD status) with appropriate HTTP caching headers (`Cache-Control: public, max-age=3600`).
- Ensure transparent error handling: if a repository does not exist or fails lookup, return an honest error SVG or 404 status (strictly zero synthetic/mock fallback personas or statistics).
- Match sponsor campaigns to the repository's primary programming language, falling back to a general sponsor if no language-specific ad is active.

### R2. Click-Through Tracking & Analytics Pipeline
- Implement redirect endpoint `/click/{ad_id}/{repo_id}` that logs click timestamp, ad ID, and repository ID, then issues an HTTP 302 redirect to the sponsor's destination URL.
- Embed click redirect hyperlinks inside the rendered SVG badge using standard SVG link elements (`<a href="..." target="_blank">`) around the ad card.
- Deduplicate or record impression events accurately without artificially inflating metrics.

### R3. Data Models & State Persistence
- Configure database models (SQLAlchemy/SQL) for:
  - Repositories: owner, name, stars, primary language, claimed status, timestamps.
  - Ads / Sponsors: sponsor name, headline, call-to-action text, click URL, target language, active status, remaining budget.
  - Impressions & Clicks: timestamps, ad references, repo references, client hash for auditability.
- Provide a database migration / table initialization mechanism and seed script to discover and register top open-source repositories from GitHub Search API.

### R4. Maintainer Onboarding & Snippet Generation
- Provide maintainer onboarding endpoints to claim repositories and generate copy-pasteable GitHub Markdown badge snippets (e.g. badge image wrapped in a link to the click redirect endpoint).
- Provide revenue share reporting and calculation endpoints reflecting a 50/50 split between maintainers and platform.

### R5. Test Suite & Verification Rig
- Provide an automated test suite verifying:
  - SVG generation produces valid XML with correct headers (`image/svg+xml`).
  - Ad selection accurately filters by target language.
  - Click redirects properly log records and return 302 to destination URL.
  - Non-existent repos or failed requests are handled transparently without fake mock data.

## Acceptance Criteria

### API & Badge Rendering
- [ ] `GET /badge/{owner}/{repo}.svg` returns HTTP 200 with `Content-Type: image/svg+xml` containing the repo name, live stars, and matched sponsor copy.
- [ ] Non-existent repository lookup returns transparent error or 404 (no fake default maintainers or synthetic data).
- [ ] Rendered SVG contains a functional clickable anchor tag linking to `/click/{ad_id}/{repo_id}`.

### Click Tracking & Redirection
- [ ] `GET /click/{ad_id}/{repo_id}` returns HTTP 302 redirect to the sponsor's `click_url`.
- [ ] Click event is persisted in the database with accurate timestamp, repo ID, and ad ID.

### Seeding & Data Management
- [ ] Database tables initialize cleanly upon startup.
- [ ] Top repositories seed script successfully runs and registers unclaimed repository inventory.

### Verification
- [ ] Automated test suite runs with all tests passing.

## 2026-10-03T00:16:16Z

Server restarted. Please resume execution and continue to Milestone 3.
