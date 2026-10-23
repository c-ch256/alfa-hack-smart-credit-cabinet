import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import Settings
from app.models import User

_password_hasher = PasswordHasher(type=Type.ID)
_dummy_hash = _password_hasher.hash(secrets.token_urlsafe(32))


class LoginRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str], deque[float]] = {}

    def allow(self, login: str, client_ip: str) -> bool:
        now = self._clock()
        keys = (
            (('account', login.casefold()), 5),
            (('ip', client_ip), 30),
            (('pair', f'{login.casefold()}\0{client_ip}'), 5),
        )
        with self._lock:
            if len(self._events) >= 10_000:
                self._events = {
                    key: events
                    for key, events in self._events.items()
                    if events and events[-1] > now - 60
                }
            if len(self._events) >= 10_000 and any(key not in self._events for key, _ in keys):
                return False
            for key, _limit in keys:
                events = self._events.setdefault(key, deque())
                while events and events[0] <= now - 60:
                    events.popleft()
            if any(len(self._events[key]) >= limit for key, limit in keys):
                return False
            for key, _limit in keys:
                self._events[key].append(now)
            return True


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    encoded_hash = (
        password_hash if password_hash and password_hash.startswith('$argon2id$') else _dummy_hash
    )
    try:
        valid = _password_hasher.verify(encoded_hash, password)
    except (InvalidHashError, VerificationError):
        return False
    return bool(valid and password_hash is not None and encoded_hash == password_hash)


def _jwt_secret(settings: Settings) -> str:
    if settings.jwt_secret is None:
        raise RuntimeError('JWT_SECRET is not configured')
    return settings.jwt_secret.get_secret_value()


def create_access_token(user: User, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {
        'sub': str(user.id),
        'iss': settings.jwt_issuer,
        'aud': settings.jwt_audience,
        'iat': now,
        'nbf': now,
        'exp': now + timedelta(minutes=settings.jwt_ttl_minutes),
        'jti': secrets.token_hex(16),
    }
    return jwt.encode(claims, _jwt_secret(settings), algorithm='HS256')


def decode_access_token(token: str, settings: Settings) -> int:
    claims = jwt.decode(
        token,
        _jwt_secret(settings),
        algorithms=['HS256'],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={'require': ['sub', 'iss', 'aud', 'iat', 'nbf', 'exp', 'jti']},
    )
    subject = claims['sub']
    if not isinstance(subject, str) or not subject.isdecimal() or int(subject) <= 0:
        raise jwt.InvalidTokenError('invalid subject')
    return int(subject)
