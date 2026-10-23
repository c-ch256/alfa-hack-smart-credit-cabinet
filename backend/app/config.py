from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='APP_', extra='forbid', hide_input_in_errors=True)

    env: Literal['local', 'test', 'production'] = 'local'
    debug: bool = False
    docs_enabled: bool = True
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ['localhost', '127.0.0.1', 'testserver']
    )
    cors_origins: list[str] = Field(default_factory=list)
    database_url: SecretStr | None = Field(default=None, validation_alias='DATABASE_URL')
    db_host: str = Field(default='localhost', min_length=1, validation_alias='DB_HOST')
    db_port: int = Field(default=5432, ge=1, le=65535, validation_alias='DB_PORT')
    db_name: str = Field(default='credit_cabinet', min_length=1, validation_alias='DB_NAME')
    db_user: str = Field(default='credit_app', min_length=1, validation_alias='DB_USER')
    db_password: SecretStr | None = Field(default=None, validation_alias='DB_PASSWORD')
    jwt_secret: SecretStr | None = Field(default=None, validation_alias='JWT_SECRET')
    jwt_issuer: str = Field(
        default='craftors-credit-api', min_length=1, validation_alias='JWT_ISSUER'
    )
    jwt_audience: str = Field(
        default='craftors-credit-cabinet', min_length=1, validation_alias='JWT_AUDIENCE'
    )
    jwt_ttl_minutes: int = Field(default=60, ge=1, le=480, validation_alias='JWT_TTL_MINUTES')
    demo_password: SecretStr | None = Field(default=None, validation_alias='DEMO_PASSWORD')
    key_rate: Decimal = Field(default=Decimal('16.00'), ge=0, le=100, validation_alias='KEY_RATE')

    @field_validator('key_rate')
    @classmethod
    def validate_key_rate(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError('KEY_RATE must be finite')
        return value

    @field_validator('jwt_secret')
    @classmethod
    def validate_jwt_secret(cls, secret: SecretStr | None) -> SecretStr | None:
        if secret is not None and len(secret.get_secret_value().encode('utf-8')) < 32:
            raise ValueError('JWT_SECRET must contain at least 32 bytes')
        return secret

    @field_validator('demo_password')
    @classmethod
    def validate_demo_password(cls, password: SecretStr | None) -> SecretStr | None:
        if password is not None and len(password.get_secret_value()) < 12:
            raise ValueError('DEMO_PASSWORD must contain at least 12 characters')
        return password

    @field_validator('allowed_hosts')
    @classmethod
    def validate_hosts(cls, hosts: list[str]) -> list[str]:
        if not hosts or any(not host or any(char in host for char in '*/: \t\n') for host in hosts):
            raise ValueError('allowed_hosts must contain explicit hostnames')
        return hosts

    @field_validator('cors_origins')
    @classmethod
    def validate_origins(cls, origins: list[str]) -> list[str]:
        if any(
            not origin.startswith(('http://', 'https://')) or '*' in origin for origin in origins
        ):
            raise ValueError('cors_origins must contain explicit HTTP(S) origins')
        return origins

    @model_validator(mode='after')
    def validate_production(self) -> 'Settings':
        if self.env != 'production':
            return self
        if self.debug or self.docs_enabled:
            raise ValueError('production requires debug and docs_enabled to be false')
        if any(host in {'localhost', '127.0.0.1', 'testserver'} for host in self.allowed_hosts):
            raise ValueError('production requires non-local allowed_hosts')
        if any(not origin.startswith('https://') for origin in self.cors_origins):
            raise ValueError('production requires HTTPS CORS origins')
        if self.database_url is None and self.db_password is None:
            raise ValueError('production requires database credentials')
        if self.jwt_secret is None:
            raise ValueError('production requires JWT_SECRET')
        return self
