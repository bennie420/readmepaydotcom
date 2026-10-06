# OpenSponsor: Open-Source README Sponsorship Platform

> Dynamic SVG README badge ad engine with live GitHub metadata, targeted sponsor campaigns, transparent click tracking, and a 50/50 maintainer revenue share.

---

## Quickstart

### 1. Launch Platform Server
```bash
python start.py --reload
```
or via PowerShell:
```powershell
.\start.ps1 -Reload
```

### 2. Access Web UI & API
- **Interactive Web App & Live Badge Studio**: [http://localhost:8080/](http://localhost:8080/)
- **Interactive API Documentation (Swagger)**: [http://localhost:8080/docs](http://localhost:8080/docs)
- **Maintainer Claiming & Onboarding**: [http://localhost:8080/?tab=maintainer](http://localhost:8080/?tab=maintainer)
- **Maintainer Revenue Ledger**: [http://localhost:8080/?tab=revenue](http://localhost:8080/?tab=revenue)

---

## Core Features

- **Dynamic SVG Badge Rendering (`/badge/{owner}/{repo}.svg`)**: Live compile SVG with real GitHub stars, primary language, CI status, and sponsor ad card with RFC 7232 ETag caching headers and HTTP 304 conditional negotiation.
- **Transparent Click Tracking (`/click/active/{repo_id}`)**: Logs click events, deduplicates client impressions with SHA-256 client audit hashing, and issues HTTP 302 redirects to the sponsor.
- **Maintainer Onboarding & Claiming (`/maintainers/claim`)**: Maintainers claim repositories, register payout addresses, and copy pre-generated Markdown, HTML, and RST badge snippets.
- **50/50 Financial Ledger (`/revenue/{repo_id}`)**: Exact `Decimal` mathematics dividing advertiser gross spend 50% to repository maintainers and 50% to platform maintenance.
- **Zero-Mock Policy**: Strictly real GitHub REST API v3 queries and authentic sponsor campaigns. No synthetic personas or fake metrics.
