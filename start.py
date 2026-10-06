#!/usr/bin/env python3
"""Open-Source README Sponsorship & Dynamic Badge Platform.

Unified Startup Script (start.py)

Usage:
    python start.py [--host 0.0.0.0] [--port 8080] [--reload] [--seed] [--no-seed] [--check-only]
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path and set as current working directory
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ANSI Color codes for clean terminal output
GREEN = "\033[92m"
BLUE = "\033[94m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# Minimum required Python version
MIN_PYTHON = (3, 12)

# Required external packages mapped to descriptions
REQUIRED_PACKAGES = {
    "fastapi": "FastAPI Web Framework",
    "uvicorn": "ASGI Server Implementation",
    "sqlalchemy": "SQLAlchemy 2.0 ORM & Database Engine",
    "httpx": "Async HTTP Client (GitHub API queries)",
    "jinja2": "Template Compiler (Dynamic SVG rendering)",
    "pydantic_settings": "Pydantic Settings v2 Configuration Engine",
}


def log_info(msg: str) -> None:
    """Log informational message."""
    print(f"{BLUE}[INFO]{RESET} {msg}")


def log_success(msg: str) -> None:
    """Log success message."""
    print(f"{GREEN}[OK]{RESET} {msg}")


def log_warn(msg: str) -> None:
    """Log warning message."""
    print(f"{YELLOW}[WARN]{RESET} {msg}")


def log_error(msg: str) -> None:
    """Log error message."""
    print(f"{RED}[ERROR]{RESET} {msg}", file=sys.stderr)


def check_python_version(min_version: tuple[int, int] = MIN_PYTHON) -> bool:
    """Ensure Python runtime meets minimum requirement (>= 3.12)."""
    current = sys.version_info[:2]
    if current < min_version:
        log_error(
            f"Unsupported Python version: {sys.version.split()[0]}. "
            f"Python {min_version[0]}.{min_version[1]}+ is required."
        )
        sys.exit(1)
    return True


def check_dependencies(packages: dict[str, str] | None = None) -> bool:
    """Verify that all required third-party libraries are installed."""
    target_pkgs = packages if packages is not None else REQUIRED_PACKAGES
    missing = []
    for pkg, desc in target_pkgs.items():
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append((pkg, desc))

    if missing:
        log_error("Missing required dependencies:")
        for pkg, desc in missing:
            print(f"  - {BOLD}{pkg}{RESET}: {desc}", file=sys.stderr)
        print(f"\n{YELLOW}Please install the required packages using:{RESET}", file=sys.stderr)
        print("  pip install -e .", file=sys.stderr)
        print("  OR:", file=sys.stderr)
        print("  pip install fastapi uvicorn sqlalchemy httpx jinja2 pydantic-settings\n", file=sys.stderr)
        sys.exit(1)
    return True


def initialize_database_and_seed(force_seed: bool = False, no_seed: bool = False, reset_ads: bool = False) -> None:
    """Initialize database tables and auto-seed if empty or explicitly requested."""
    from sqlalchemy import func, select

    from app.database import SessionLocal, engine, init_db
    from app.models.ad import Ad
    from app.models.repository import Repository

    log_info("Verifying SQLite schema & table initialization (WAL mode & PRAGMAs)...")
    init_db(engine)
    log_success("Database schema initialized.")

    if no_seed:
        log_info("Skipping database seeding checks (--no-seed flag provided).")
        return

    with SessionLocal() as db:
        repo_count = db.scalar(select(func.count(Repository.id))) or 0
        ad_count = db.scalar(select(func.count(Ad.id))) or 0

        # 1. Sponsor Ads Seeding
        if ad_count == 0 or force_seed or reset_ads:
            log_info("Seeding authentic tech sponsor campaigns (Sentry, Neon, Supabase, Docker, GitHub Sponsors, PostHog)...")
            from scripts.seed_sample_ads import seed_sample_ads

            seeded_ads = seed_sample_ads(db, reset=reset_ads)
            log_success(f"Seeded {seeded_ads} authentic sponsor campaigns into database.")
        else:
            log_info(f"Existing sponsor campaigns found ({ad_count} campaigns active).")

        # 2. Top Repositories Seeding
        if repo_count == 0 or force_seed:
            log_info("Checking repository inventory: Seeding top open-source repositories from GitHub Search API...")
            try:
                from scripts.seed_top_repos import seed_top_repositories

                token = os.getenv("GITHUB_TOKEN")
                if token:
                    log_info("Using GITHUB_TOKEN from environment for higher rate limits.")
                else:
                    log_info("No GITHUB_TOKEN set; querying public GitHub Search API (rate limits handled gracefully).")

                seeded_repos = seed_top_repositories(db, limit_per_lang=5)
                log_success(f"Processed {seeded_repos} top open-source repositories.")
            except Exception as exc:
                log_warn(f"GitHub repository seeding encountered a non-fatal warning: {exc}")
                log_info("Repositories will still be fetched and cached live on-demand via /badge/{owner}/{repo}.svg.")
        else:
            log_info(f"Existing repository inventory found ({repo_count} repositories registered).")


def get_banner_text(host: str, port: int, reload_enabled: bool) -> str:
    """Generate an attractive ASCII banner with active endpoints."""
    # Compute clickable display host
    display_host = "localhost" if host in ("0.0.0.0", "::") else host
    base_url = f"http://{display_host}:{port}"

    return f"""
{BOLD}{CYAN}================================================================================{RESET}
{BOLD}{CYAN}   OPEN-SOURCE README SPONSORSHIP & DYNAMIC BADGE PLATFORM                      {RESET}
{BOLD}{CYAN}================================================================================{RESET}
  {DIM}Architecture:{RESET} FastAPI + Uvicorn | SQLAlchemy 2.0 (SQLite WAL) | Jinja2 SVG Engine
  {DIM}Listening on:{RESET} {BOLD}{GREEN}{base_url}{RESET} {DIM}(Bound to {host}:{port}){RESET}
  {DIM}Auto-Reload: {RESET} {"Enabled" if reload_enabled else "Disabled"}
  {DIM}Zero-Mock:   {RESET} Active (Strictly real GitHub metadata & authentic sponsor campaigns)
{BOLD}{CYAN}--------------------------------------------------------------------------------{RESET}
  {BOLD}EXPLORE THE PLATFORM ENDPOINTS:{RESET}

  {BOLD}1. Interactive API Documentation:{RESET}
     {CYAN}-> Swagger UI:{RESET}     {base_url}/docs
     {CYAN}-> ReDoc:{RESET}          {base_url}/redoc
     {CYAN}-> OpenAPI Schema:{RESET} {base_url}/openapi.json

  {BOLD}2. Core System Endpoints:{RESET}
     {CYAN}-> Health Check:{RESET}   {base_url}/health
     {CYAN}-> Root Status:{RESET}    {base_url}/

  {BOLD}3. Live Dynamic SVG Badges (Jinja2 + Real GitHub Metadata):{RESET}
     {CYAN}-> FastAPI Badge:{RESET}  {base_url}/badge/tiangolo/fastapi.svg
     {CYAN}-> Flask Badge:{RESET}    {base_url}/badge/pallets/flask.svg
     {CYAN}-> Rust Ripgrep:{RESET}   {base_url}/badge/BurntSushi/ripgrep.svg

  {BOLD}4. Transparent Click Tracking & 302 Redirection:{RESET}
     {CYAN}-> Active Click:{RESET}   {base_url}/click/active/1

  {BOLD}5. Maintainer Onboarding & Markdown Snippets:{RESET}
     {CYAN}-> Snippet Gen:{RESET}    {base_url}/maintainers/snippet/tiangolo/fastapi
     {CYAN}-> Claim Repo:{RESET}     {base_url}/maintainers/claim

  {BOLD}6. Financial Ledger & 50/50 Revenue Split Reporting:{RESET}
     {CYAN}-> Repo Revenue:{RESET}   {base_url}/revenue/1
{BOLD}{CYAN}================================================================================{RESET}
  {YELLOW}[Press Ctrl+C to stop the server]{RESET}
"""


def print_banner(host: str, port: int, reload_enabled: bool) -> None:
    """Display the ASCII banner."""
    print(get_banner_text(host=host, port=port, reload_enabled=reload_enabled))


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    from app.config import settings

    parser = argparse.ArgumentParser(
        description="Launch Open-Source README Sponsorship & Dynamic Badge Platform.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("HOST", settings.HOST),
        help="Host interface to bind the Uvicorn server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", str(settings.PORT))),
        help="Port number to bind the Uvicorn server",
    )
    parser.add_argument(
        "--reload",
        dest="reload",
        action="store_true",
        default=False,
        help="Enable auto-reload on code modifications",
    )
    parser.add_argument(
        "--no-reload",
        dest="reload",
        action="store_false",
        help="Disable auto-reload",
    )
    parser.add_argument(
        "--seed",
        dest="seed",
        action="store_true",
        default=False,
        help="Force execution of database seeders even if data exists",
    )
    parser.add_argument(
        "--no-seed",
        dest="no_seed",
        action="store_true",
        default=False,
        help="Skip database seeding checks entirely",
    )
    parser.add_argument(
        "--reset-ads",
        dest="reset_ads",
        action="store_true",
        default=False,
        help="Reset existing sponsor ad budgets to original amounts during seeding",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        default=False,
        help="Run environment verification and database initialization then exit",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level",
    )

    return parser.parse_args(args)


def main(argv: list[str] | None = None) -> int:
    """Main entrypoint for start.py."""
    # 1. Environment & Preflight checks
    check_python_version()
    check_dependencies()

    # 2. Parse CLI arguments
    args = parse_arguments(argv)

    # 3. Database initialization and seeding
    initialize_database_and_seed(force_seed=args.seed, no_seed=args.no_seed, reset_ads=args.reset_ads)

    # 4. If check-only flag is set, exit cleanly
    if args.check_only:
        log_success("All environment, database, and dependency preflight checks passed.")
        return 0

    # 5. Display banner
    print_banner(host=args.host, port=args.port, reload_enabled=args.reload)

    # 6. Execute Uvicorn server
    import uvicorn

    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
    except KeyboardInterrupt:
        print()
        log_info("Keyboard interrupt received (Ctrl+C). Shutting down platform...")
    except Exception as exc:
        log_error(f"Uvicorn server exited with error: {exc}")
        return 1

    log_success("Open-Source Sponsorship Platform stopped cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
