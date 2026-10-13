"""Application factory.

Run with:  uvicorn app.main:create_app --factory
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pyproj import network
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.files import router as files_router
from app.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import RateLimitMiddleware, SecurityHeadersMiddleware, UploadSizeLimitMiddleware
from app.database import create_session_factory
from app.models import Base

logger = logging.getLogger("geo_api")
WEB_DIR = Path(__file__).parent / "web"

DESCRIPTION = """
Upload a **Shapefile (.zip)** or **KML** and get back every feature with its geometry type,
CRS and attributes, plus **area** (polygons) and **length** (lines).

Measurements are never computed in degrees: each feature is re-projected to the UTM zone
that contains it, and an ellipsoidal (geodesic) value is returned as an independent cross-check.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Never let PROJ download grid files over the network while handling untrusted input.
    network.set_network_enabled(False)

    engine, session_factory = create_session_factory(settings.database_url)
    Base.metadata.create_all(engine)

    if not settings.api_key_list:
        logger.warning("API_KEYS is empty: authentication is DISABLED. Set it before exposing this service.")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=DESCRIPTION,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory

    # Middleware order: the LAST one added is the OUTERMOST (runs first).
    app.add_middleware(UploadSizeLimitMiddleware, max_bytes=settings.max_upload_bytes)
    app.add_middleware(
        RateLimitMiddleware,
        general_limit=settings.rate_limit_per_minute,
        upload_limit=settings.upload_rate_limit_per_minute,
    )
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["X-API-Key", "Content-Type"],
        )
    if settings.allowed_host_list != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception):
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/", include_in_schema=False)
    def root():
        if settings.enable_ui:
            return FileResponse(WEB_DIR / "index.html")
        return RedirectResponse(url="/docs" if settings.enable_docs else "/health")

    if settings.enable_ui:
        app.mount("/ui", StaticFiles(directory=WEB_DIR), name="ui")

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    def health():
        return {"status": "ok", "version": settings.version}

    app.include_router(files_router)
    return app
