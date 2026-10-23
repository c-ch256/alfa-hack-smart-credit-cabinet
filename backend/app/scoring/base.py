from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

RiskBand = Literal['A', 'B', 'C', 'D']
FEATURE_SCHEMA_VERSION = 'scoring-request-1'
RISK_BAND_B_FROM = Decimal('0.05')
RISK_BAND_C_FROM = Decimal('0.10')
RISK_BAND_D_FROM = Decimal('0.20')


def risk_band_for_pd(pd: Decimal) -> RiskBand:
    if pd < RISK_BAND_B_FROM:
        return 'A'
    if pd < RISK_BAND_C_FROM:
        return 'B'
    if pd < RISK_BAND_D_FROM:
        return 'C'
    return 'D'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CompanyFeatures(StrictModel):
    legal_form: Literal['OOO', 'IP']
    okved: str = Field(min_length=1, max_length=16)
    region: str = Field(min_length=1, max_length=128)
    business_age_months: int = Field(ge=0, le=1200)
    bank_client_months: int = Field(ge=0, le=1200)
    avg_monthly_revenue: int = Field(ge=0)
    avg_monthly_expenses: int = Field(ge=0)
    revenue_trend_6m: Decimal = Field(strict=False, ge=Decimal('-1'), le=Decimal('10'))
    existing_monthly_payments: int = Field(ge=0)


class BkiFeatures(StrictModel):
    active_loans: int = Field(ge=0, le=1000)
    max_overdue_days_12m: int = Field(ge=0, le=365)
    total_debt: int = Field(ge=0, le=9_223_372_036_854_775_807)
    defaults: int = Field(ge=0, le=1000)


class FnsFeatures(StrictModel):
    status: Literal['active', 'liquidating', 'inactive']
    tax_debt: bool
    mass_address: bool


class ScoringRequest(StrictModel):
    product_code: Literal['working_capital', 'investment']
    amount: int = Field(gt=0, le=9_223_372_036_854_775_807)
    term_months: int = Field(ge=1, le=84)
    collateral_value: int = Field(ge=0, le=9_223_372_036_854_775_807)
    company: CompanyFeatures
    bki: BkiFeatures
    fns: FnsFeatures


class ScoreReason(StrictModel):
    feature: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=255)
    effect: Literal['increases_risk', 'decreases_risk']
    contribution: Decimal = Field(strict=False)


class ScoringResult(StrictModel):
    pd: Decimal = Field(strict=False, ge=0, le=1)
    risk_band: RiskBand
    model_version: str = Field(min_length=1, max_length=64)
    reasons: list[ScoreReason] = Field(max_length=20)

    @model_validator(mode='after')
    def consistent_band(self) -> 'ScoringResult':
        if self.risk_band != risk_band_for_pd(self.pd):
            raise ValueError('risk_band does not match pd')
        return self


class Scorer(Protocol):
    def score(self, req: ScoringRequest) -> ScoringResult: ...
