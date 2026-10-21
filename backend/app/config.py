from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='APP_', extra='forbid')

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
        return self
