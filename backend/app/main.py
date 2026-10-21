from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.health import router as health_router
from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    app = FastAPI(
        title='Smart Credit Cabinet API',
        debug=config.debug,
        docs_url='/docs' if config.docs_enabled else None,
        redoc_url=None,
        openapi_url='/openapi.json' if config.docs_enabled else None,
    )
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
    return app


app = create_app()
