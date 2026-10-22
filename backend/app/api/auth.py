from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_session
from app.errors import ApiError
from app.models import User
from app.security import create_access_token, verify_password

router = APIRouter(prefix='/auth', tags=['auth'])
BEARER = 'bearer'


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    login: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator('login')
    @classmethod
    def strip_login(cls, login: str) -> str:
        clean_login = login.strip()
        if not clean_login:
            raise ValueError('login must not be empty')
        return clean_login


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal['bearer']
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    role: Literal['CLIENT', 'STAFF']
    full_name: str
    position: str | None


@router.post('/login', response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> TokenResponse:
    settings = request.app.state.settings
    if settings.jwt_secret is None:
        raise ApiError(503, 'AUTH_UNAVAILABLE', 'Авторизация недоступна')
    client_ip = request.client.host if request.client else 'unknown'
    if not request.app.state.login_limiter.allow(payload.login, client_ip):
        raise ApiError(
            429,
            'TOO_MANY_ATTEMPTS',
            'Слишком много попыток входа',
            {'Retry-After': '60'},
        )
    try:
        user = session.scalar(select(User).where(User.login == payload.login))
    except SQLAlchemyError:
        raise ApiError(503, 'AUTH_UNAVAILABLE', 'Авторизация недоступна') from None
    password_valid = verify_password(
        payload.password.get_secret_value(), user.password_hash if user else None
    )
    if not password_valid or user is None or not user.is_active:
        raise ApiError(
            401,
            'INVALID_CREDENTIALS',
            'Неверный логин или пароль',
            {'WWW-Authenticate': 'Bearer'},
        )
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    return TokenResponse(
        access_token=create_access_token(user, settings),
        token_type=BEARER,
        expires_in=settings.jwt_ttl_minutes * 60,
    )


@router.get('/me', response_model=UserResponse)
async def me(response: Response, user: Annotated[User, Depends(get_current_user)]) -> User:
    response.headers['Cache-Control'] = 'no-store'
    return user
