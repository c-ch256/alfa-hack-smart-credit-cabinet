from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.application_schemas import (
    ApplicationResponse,
    CompanyResponse,
    OfferResponse,
    ProductResponse,
    StatusEventResponse,
    StrictInput,
)


class StaffNotification(BaseModel):
    id: int
    application_id: int
    application_number: str
    company_name: str
    kind: Literal['assigned', 'additional_info', 'offer_accepted', 'offer_declined', 'withdrawn']
    created_at: datetime


class StaffProductCatalog(BaseModel):
    products: list[ProductResponse]
    key_rate: Decimal
    risk_premium_pp: dict[str, Decimal]
    rate_formula: Literal['key_rate + margin_pp + risk_premium_pp']
    demonstration: bool


class LoanPreviewInput(StrictInput):
    application_id: int = Field(strict=True, gt=0, le=2_147_483_647)
    product_code: str = Field(min_length=1, max_length=32, pattern=r'^[a-z_]+$')
    amount: int = Field(strict=True, gt=0, le=9_223_372_036_854_775_807)
    term_months: int = Field(strict=True, gt=0, le=1200)


class ProductLimits(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    min_amount: int
    max_amount: int
    min_term_months: int
    max_term_months: int


class LoanPreviewResponse(BaseModel):
    application_id: int
    snapshot_id: int
    snapshot_version: int
    product: ProductResponse
    amount: int
    term_months: int
    risk_band: Literal['A', 'B', 'C', 'D']
    key_rate: Decimal
    risk_premium_pp: Decimal
    pd: Decimal
    model_version: str
    score_source: Literal['mock', 'model']
    is_test_result: bool
    scoring_reasons: list[dict[str, Any]]
    annual_rate: Decimal
    monthly_payment: int
    dscr: Decimal
    total_payments: int
    overpayment: int
    applied_constraints: ProductLimits
    indicative: bool
    demonstration: bool
    explanation: str


class StaffCommentInput(StrictInput):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator('body')
    @classmethod
    def nonblank_body(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError('Укажите текст комментария')
        return trimmed


class StaffCommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    author_user_id: int
    body: str
    created_at: datetime
    audit_event_id: int | None = None
    status_event_id: int | None = None


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


RequestInfoField = Literal[
    'purpose_details', 'investment_object', 'collateral_type', 'collateral_value'
]


class RequestInfoInput(StrictInput):
    fields: list[RequestInfoField] = Field(min_length=1, max_length=4)
    comment: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def validate_request(self) -> 'RequestInfoInput':
        if not self.comment.strip():
            raise ValueError('Укажите внутренний комментарий')
        if len(set(self.fields)) != len(self.fields):
            raise ValueError('Поля запроса не должны повторяться')
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
    snapshot_id: int
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


class StaffDscrResponse(BaseModel):
    snapshot_id: int
    score_id: int | None = None
    key_rate: Decimal | None = None
    monthly_payment: int | None
    calculation_basis: Literal['current_formula', 'saved_offer']
    scope: Literal['original_application', 'offer']
    offer_id: int | None
    snapshot_version: int
    product_code: str | None
    amount: int | None
    term_months: int | None
    rate: Decimal | None
    value: Decimal | None
    explanation: str | None


class StaffDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    snapshot_id: int
    score_id: int | None
    decision: Literal['approve', 'counter', 'reject']
    created_by: Literal['SYSTEM', 'STAFF']
    created_by_user_id: int | None
    rule_version: str
    rationale: list[str]
    created_at: datetime
    internal_comment: str | None = None


class StaffInfoChange(BaseModel):
    field: Literal[
        'purpose_details', 'investment_object', 'collateral_type', 'collateral_value', 'message'
    ]
    before: str | int | None
    after: str | int | None


class StaffInfoResponse(BaseModel):
    request_event_id: int | None
    response_event_id: int
    answered_at: datetime
    requested_fields: list[RequestInfoField] | None
    changes: list[StaffInfoChange] | None


class StaffInitialRequest(BaseModel):
    snapshot_id: int
    snapshot_version: int
    submitted_at: datetime
    product_code: str
    amount: int
    term_months: int
    purpose: str
    purpose_details: str | None
    investment_object: str | None
    collateral_type: str | None
    collateral_value: int | None


StaffAction = Literal['approve', 'counter', 'reject', 'request_info']


class StaffApplicationResponse(ApplicationResponse):
    assigned_staff_user_id: int | None


StaffWorkCategory = Literal[
    'manual_review',
    'needs_info',
    'waiting_offer',
    'staff_approved',
    'staff_rejected',
    'staff_offer_accepted',
]


class StaffListCompany(BaseModel):
    name: str
    inn: str


class StaffApplicationListResponse(ApplicationResponse):
    company: StaffListCompany
    risk_band: Literal['A', 'B', 'C', 'D'] | None
    received_additional_info: bool
    work_categories: list[StaffWorkCategory]


class StaffStatusEventResponse(StatusEventResponse):
    actor_display_name: str | None


class StaffApplicationDetail(BaseModel):
    application: StaffApplicationResponse
    initial_request: StaffInitialRequest | None
    allowed_actions: list[StaffAction]
    additional_info_responses: list[StaffInfoResponse]
    company: CompanyResponse
    snapshot_id: int
    snapshot_version: int
    company_snapshot: dict[str, Any]
    checks: list[StaffCheckResponse]
    score: StaffScoreResponse | None
    dscr: list[StaffDscrResponse]
    offers: list[OfferResponse]
    history: list[StaffStatusEventResponse]
    decisions: list[StaffDecisionResponse]
    comments: list[StaffCommentResponse]


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
    decision_source: Literal['SYSTEM', 'STAFF'] | None = None
    internal_comment: str | None = None
    actor_display_name: str | None = None
    company_name: str | None = None
    status_at_event: str | None = None
    decision_id: int | None = None
    status_event_id: int | None = None
    comment_id: int | None = None
    comment_author_user_id: int | None = None
    comment_created_at: datetime | None = None
    offer_id: int | None = None
    offer_terms: dict[str, Any] | None = None
    additional_info: StaffInfoResponse | None = None
    info_request_text: str | None = None
    assigned_staff_user_id: int | None = None
    assigned_staff_display_name: str | None = None
