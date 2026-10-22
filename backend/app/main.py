from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import Engine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.applications import router as applications_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.ready import router as ready_router
from app.config import Settings
from app.db import create_db_engine
from app.errors import ApiError, api_error_handler, validation_error_handler
from app.security import LoginRateLimiter


def create_app(settings: Settings | None = None, *, db_engine: Engine | None = None) -> FastAPI:
    config = settings or Settings()
    app = FastAPI(
        title='Smart Credit Cabinet API',
        debug=config.debug,
        docs_url='/docs' if config.docs_enabled else None,
        redoc_url=None,
        openapi_url='/openapi.json' if config.docs_enabled else None,
    )
    app.state.settings = config
    app.state.db_engine = db_engine if db_engine is not None else create_db_engine(config)
    app.state.login_limiter = LoginRateLimiter()

    @app.middleware('http')
    async def prevent_response_caching(request: Request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=False,
            allow_methods=['GET', 'POST', 'PATCH'],
            allow_headers=['Accept', 'Authorization', 'Content-Type', 'Idempotency-Key'],
        )
    app.include_router(health_router)
    app.include_router(ready_router)
    app.include_router(auth_router)
    app.include_router(applications_router)
    return app


app = create_app()
