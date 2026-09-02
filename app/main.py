import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.routers import users, webhooks


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="account-service",
        description="Mirrors Clerk users into Postgres and serves profile data internally.",
        version="0.1.0",
        lifespan=lifespan,
        # Schema docs are useful locally but this service is not publicly routed.
        docs_url="/docs" if settings.is_local else None,
        redoc_url=None,
    )

    app.include_router(webhooks.router)
    app.include_router(users.router)

    @app.get("/health", tags=["ops"])
    async def health() -> JSONResponse:
        """Readiness probe for Railway. Fails when Postgres is unreachable."""
        try:
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            logging.getLogger(__name__).exception("health check failed")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "database": "down"},
            )
        return JSONResponse(content={"status": "ok", "database": "up"})

    return app


app = create_app()
