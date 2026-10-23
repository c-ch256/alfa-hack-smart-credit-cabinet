from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db import database_is_ready

router = APIRouter()


@router.get('/ready', tags=['health'], response_model=None)
async def ready(request: Request) -> dict[str, str] | JSONResponse:
    if database_is_ready(request.app.state.db_engine):
        return {'status': 'ok'}
    return JSONResponse(status_code=503, content={'status': 'unavailable'})
