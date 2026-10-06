"""
FastAPI Application Factory, Lifespan Setup, and Routing Engine (app/main.py).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.database import engine, init_db


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager: handles startup and shutdown."""
    # 1. Startup: Initialize database schema (WAL mode & PRAGMAs configured)
    init_db(engine)

    # 2. Yield control to running application
    yield

    # 3. Shutdown: Clean up any shared HTTP client sessions
    try:
        from app.services.github_service import close_github_client
        await close_github_client()
    except (ImportError, AttributeError):
        pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    from app.config import settings

    docs_url = "/docs" if getattr(settings, "ENABLE_DOCS", False) else None
    redoc_url = "/redoc" if getattr(settings, "ENABLE_DOCS", False) else None
    openapi_url = "/openapi.json" if getattr(settings, "ENABLE_DOCS", False) else None

    app_instance = FastAPI(
        title="Open-Source README Sponsorship Platform",
        description="Dynamic SVG README badge ad engine with GitHub metadata and transparent analytics.",
        version="1.0.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    # 1. Public CORS Middleware
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Static Files Mounting (if static directory exists)
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app_instance.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 3. Router Inclusions
    # Milestone 2: Dynamic Badge Router
    try:
        from app.routers.badge import router as badge_router
        app_instance.include_router(badge_router)
    except (ImportError, AttributeError):
        pass

    # Milestone 3: Click Tracking Router
    from app.routers.click import router as click_router
    app_instance.include_router(click_router)

    # Milestone 4: Maintainer Onboarding Router
    from app.routers.maintainers import router as maintainers_router
    app_instance.include_router(maintainers_router)

    # Milestone 4: Revenue Share Reporting Router
    from app.routers.revenue import router as revenue_router
    app_instance.include_router(revenue_router)

    # Inventory & Directory Router
    try:
        from app.routers.inventory import router as inventory_router
        app_instance.include_router(inventory_router)
    except (ImportError, AttributeError):
        pass

    # Billing & Payouts Router (PayPal & Crypto)
    from app.routers.billing import router as billing_router
    app_instance.include_router(billing_router)

    # GitHub OAuth & Maintainer Identity Verification Router
    from app.routers.auth import router as auth_router
    app_instance.include_router(auth_router)

    # 4. Core System Endpoints
    @app_instance.get("/health", tags=["System"])
    async def health_check():
        return {
            "status": "healthy",
            "version": "1.0.0",
            "database": "sqlite_wal",
        }

    @app_instance.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        favicon_path = Path(__file__).resolve().parent / "static" / "favicon.svg"
        if favicon_path.exists():
            return Response(
                content=favicon_path.read_text(encoding="utf-8"),
                media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def get_ui_html() -> str:
        template_file = Path(__file__).resolve().parent / "templates" / "index.html"
        if template_file.exists():
            return template_file.read_text(encoding="utf-8")
        return "<h1>ReadmePay Platform</h1><p>Dynamic README Badge Engine.</p>"

    @app_instance.get("/app", include_in_schema=False)
    @app_instance.get("/dashboard", include_in_schema=False)
    async def web_ui_dashboard():
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=get_ui_html())

    @app_instance.get("/", tags=["System"])
    async def root(request: Request):
        accept = request.headers.get("accept", "")
        # If requested by a web browser, serve the interactive Single Page UI
        if "text/html" in accept and "application/json" not in accept:
            from fastapi.responses import HTMLResponse
            return HTMLResponse(content=get_ui_html())
        return {
            "name": "Open-Source README Sponsorship Platform",
            "badge_docs": "/docs",
            "example_badge": "/badge/pallets/flask.svg",
        }

    # 5. Transparent Error Handling for SVG Paths
    @app_instance.exception_handler(404)
    async def custom_404_handler(request: Request, exc):
        path = request.url.path
        if path.endswith(".svg"):
            try:
                from app.services.badge_service import build_error_svg
                error_svg = build_error_svg(
                    error_title="404 Not Found",
                    error_message="The requested repository or badge resource could not be found.",
                    error_code=404,
                )
                return Response(
                    content=error_svg,
                    status_code=status.HTTP_404_NOT_FOUND,
                    media_type="image/svg+xml; charset=utf-8",
                    headers={
                        "Content-Type": "image/svg+xml; charset=utf-8",
                        "Cache-Control": "public, max-age=60",
                    },
                )
            except (ImportError, AttributeError):
                pass
        detail = getattr(exc, "detail", None) or "Not Found"
        headers = getattr(exc, "headers", None)
        return JSONResponse(status_code=404, content={"detail": detail}, headers=headers)

    return app_instance


# Module-level application instance for test client imports and ASGI servers
app = create_app()
