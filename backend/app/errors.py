from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.headers = headers


async def api_error_handler(_request: Request, error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={'detail': error.detail, 'code': error.code},
        headers={'Cache-Control': 'no-store', **(error.headers or {})},
    )


async def validation_error_handler(
    _request: Request, _error: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={'detail': 'Некорректные данные запроса', 'code': 'VALIDATION_ERROR'},
        headers={'Cache-Control': 'no-store'},
    )
