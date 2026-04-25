"""FastAPI app entrypoint. Imports from `shared` for everything cross-cutting."""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from shared.core.config import get_settings
from shared.core.logging import configure_logging
from shared.services.github import close_client

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    log = structlog.get_logger("app.lifespan")
    log.info("app.startup", env=settings.env)
    yield
    await close_client()
    log.info("app.shutdown")


app = FastAPI(
    title="AI DevOps SaaS",
    version="0.1.0",
    docs_url="/docs" if settings.env != "prod" else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log = structlog.get_logger(__name__)
    log.exception("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
