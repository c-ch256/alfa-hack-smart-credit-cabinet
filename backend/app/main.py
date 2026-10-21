from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import Engine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.health import router as health_router
from app.api.ready import router as ready_router
from app.config import Settings
from app.db import create_db_engine


def create_app(settings: Settings | None = None, *, db_engine: Engine | None = None) -> FastAPI:
    config = settings or Settings()
    app = FastAPI(
        title='Smart Credit Cabinet API',
        debug=config.debug,
        docs_url='/docs' if config.docs_enabled else None,
        redoc_url=None,
        openapi_url='/openapi.json' if config.docs_enabled else None,
    )
    app.state.db_engine = db_engine if db_engine is not None else create_db_engine(config)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=False,
            allow_methods=['GET'],
            allow_headers=['Accept', 'Content-Type'],
        )
    app.include_router(health_router)
    app.include_router(ready_router)
    return app


app = create_app()
