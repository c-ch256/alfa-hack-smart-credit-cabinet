"""Один выбор адаптера для автоматической и ручной оценки."""

from collections.abc import Mapping
from decimal import Decimal

from app.config import Settings
from app.scoring.base import Scorer
from app.scoring.mock import MockScorer


class ScorerUnavailableError(RuntimeError):
    """Настроенный адаптер недоступен; кредитное решение запрещено."""


def select_scorer(
    settings: Settings, configured: Scorer | None, product_margins: Mapping[str, Decimal]
) -> Scorer:
    if settings.scorer == 'model':
        if configured is None or getattr(configured, 'source', None) != 'model':
            raise ScorerUnavailableError('model scorer unavailable')
        return configured
    if configured is not None:
        return configured
    return MockScorer(settings.key_rate, product_margins)
