"""Версионированные локальные синтетические ответы ФНС и БКИ для демо-ИНН."""

from copy import deepcopy

MOCK_VERSION = 'synthetic-2026-1'

_FNS = {
    '0000000000': {'status': 'active', 'tax_debt': False, 'mass_address': False},
    '000000000001': {'status': 'active', 'tax_debt': False, 'mass_address': False},
    '0000000002': {'status': 'active', 'tax_debt': True, 'mass_address': True},
    '0000000003': {'status': 'liquidating', 'tax_debt': False, 'mass_address': False},
}

_BKI = {
    '0000000000': {
        'active_loans': 1,
        'total_debt': 1_200_000,
        'max_overdue_days_12m': 0,
        'defaults': 0,
    },
    '000000000001': {
        'active_loans': 1,
        'total_debt': 600_000,
        'max_overdue_days_12m': 5,
        'defaults': 0,
    },
    '0000000002': {
        'active_loans': 3,
        'total_debt': 3_000_000,
        'max_overdue_days_12m': 45,
        'defaults': 0,
    },
    '0000000003': {
        'active_loans': 2,
        'total_debt': 2_000_000,
        'max_overdue_days_12m': 120,
        'defaults': 0,
    },
}


class MockUnavailableError(Exception):
    pass


def fns_by_inn(inn: str) -> dict:
    if inn not in _FNS:
        raise MockUnavailableError('synthetic FNS profile unavailable')
    return deepcopy(_FNS[inn])


def bki_by_inn(inn: str) -> dict:
    if inn not in _BKI:
        raise MockUnavailableError('synthetic BKI profile unavailable')
    return deepcopy(_BKI[inn])
