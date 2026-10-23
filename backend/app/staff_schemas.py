from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application_schemas import (
    ApplicationResponse,
    CompanyResponse,
    OfferResponse,
    StatusEventResponse,
    StrictInput,
)


class ManualDecisionInput(StrictInput):
    decision: Literal['approve', 'counter', 'reject']
    amount: int | None = Field(default=None, strict=True, gt=0)
    term_months: int | None = Field(default=None, strict=True, gt=0)
    product_code: Literal['working_capital', 'investment'] | None = None
    comment: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def validate_fields(self) -> 'ManualDecisionInput':
        if not self.comment.strip():
            raise ValueError('Укажите комментарий сотрудника')
        if self.decision != 'counter' and any(
            value is not None for value in (self.amount, self.term_months, self.product_code)
        ):
            raise ValueError('Условия можно изменить только для встречного предложения')
        if self.decision == 'counter' and all(
            value is None for value in (self.amount, self.term_months, self.product_code)
        ):
            raise ValueError('Укажите изменяемые условия')
        return self


class RequestInfoInput(StrictInput):
    message: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def validate_message(self) -> 'RequestInfoInput':
        if not self.message.strip():
            raise ValueError('Укажите запрашиваемые сведения')
        return self


class StaffCheckResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    status: str
    summary: str
    payload: dict[str, Any]
    adapter_version: str
    created_at: datetime


class StaffScoreResponse(BaseModel):
    id: int
    source: str
    result_status: str
    is_test_result: bool
    pd: Decimal | None
    risk_band: str | None
    model_version: str
    reasons: list[dict[str, Any]]
    error_code: str | None
    product_code: str | None
    amount: int | None
    term_months: int | None
    created_at: datetime


class StaffApplicationDetail(BaseModel):
    application: ApplicationResponse
    company: CompanyResponse
    snapshot_version: int
    company_snapshot: dict[str, Any]
    checks: list[StaffCheckResponse]
    score: StaffScoreResponse | None
    offers: list[OfferResponse]
    history: list[StatusEventResponse]


class StaffAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None
    actor_role: str
    application_id: int | None
    action: str
    result: str
    rule_version: str | None
    details: dict[str, Any]
    created_at: datetime
