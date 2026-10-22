from collections.abc import AsyncIterator, Callable
from typing import Annotated, Literal

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import User
from app.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request) -> AsyncIterator[Session]:
    with Session(request.app.state.db_engine) as session:
        yield session


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise ApiError(401, 'UNAUTHORIZED', 'Требуется авторизация', {'WWW-Authenticate': 'Bearer'})
    if len(credentials.credentials) > 4096:
        raise ApiError(401, 'UNAUTHORIZED', 'Требуется авторизация', {'WWW-Authenticate': 'Bearer'})
    try:
        user_id = decode_access_token(credentials.credentials, request.app.state.settings)
    except RuntimeError:
        raise ApiError(503, 'AUTH_UNAVAILABLE', 'Авторизация недоступна') from None
    except jwt.InvalidTokenError:
        raise ApiError(
            401, 'UNAUTHORIZED', 'Требуется авторизация', {'WWW-Authenticate': 'Bearer'}
        ) from None
    try:
        user = session.get(User, user_id)
    except SQLAlchemyError:
        raise ApiError(503, 'AUTH_UNAVAILABLE', 'Авторизация недоступна') from None
    if user is None or not user.is_active:
        raise ApiError(401, 'UNAUTHORIZED', 'Требуется авторизация', {'WWW-Authenticate': 'Bearer'})
    return user


def require_role(role: Literal['CLIENT', 'STAFF']) -> Callable[..., User]:
    async def dependency(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role != role:
            raise ApiError(403, 'FORBIDDEN', 'Недостаточно прав')
        return user

    return dependency
