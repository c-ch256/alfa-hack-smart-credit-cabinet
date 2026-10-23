from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ApplicationCreate(StrictInput):
    company_id: int = Field(strict=True, gt=0, le=2_147_483_647)
    product_code: Literal['working_capital', 'investment']
    amount: int = Field(strict=True, gt=0)
    term_months: int = Field(strict=True, gt=0)
    purpose: str = Field(min_length=1, max_length=128)
    purpose_details: str | None = Field(default=None, max_length=2000)
    investment_object: str | None = Field(default=None, max_length=255)
    collateral_type: str | None = Field(default=None, max_length=128)
    collateral_value: int | None = Field(
        default=None, strict=True, ge=0, le=9_223_372_036_854_775_807
    )


class ApplicationPatch(StrictInput):
    product_code: Literal['working_capital', 'investment'] | None = None
    amount: int | None = Field(default=None, strict=True, gt=0)
    term_months: int | None = Field(default=None, strict=True, gt=0)
    purpose: str | None = Field(default=None, min_length=1, max_length=128)
    purpose_details: str | None = Field(default=None, max_length=2000)
    investment_object: str | None = Field(default=None, max_length=255)
    collateral_type: str | None = Field(default=None, max_length=128)
    collateral_value: int | None = Field(
        default=None, strict=True, ge=0, le=9_223_372_036_854_775_807
    )

    @model_validator(mode='after')
    def required_change(self) -> 'ApplicationPatch':
        if not self.model_fields_set:
            raise ValueError('Укажите поля для изменения')
        for name in ('product_code', 'amount', 'term_months', 'purpose'):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f'{name} не может быть пустым')
        return self


class AdditionalInfo(StrictInput):
    version: int = Field(strict=True, ge=1)
    message: str | None = Field(default=None, max_length=2000)
    purpose_details: str | None = Field(default=None, max_length=2000)
    investment_object: str | None = Field(default=None, max_length=255)
    collateral_type: str | None = Field(default=None, max_length=128)
    collateral_value: int | None = Field(
        default=None, strict=True, gt=0, le=9_223_372_036_854_775_807
    )

    @model_validator(mode='after')
    def has_details(self) -> 'AdditionalInfo':
        fields = self.model_dump(exclude_unset=True, exclude={'version'})
        if not fields or any(
            value is None or isinstance(value, str) and not value.strip()
            for value in fields.values()
        ):
            raise ValueError('Укажите дополнительные сведения')
        return self


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    inn: str
    legal_form: str
    okved: str
    industry: str | None
    region: str
    registered_at: date
    bank_client_since: date
    avg_monthly_revenue: int
    avg_monthly_expenses: int
    revenue_trend_6m: Decimal
    existing_monthly_payments: int


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    terms_version: str
    min_amount: int
    max_amount: int
    min_term_months: int
    max_term_months: int
    margin_pp: Decimal


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    company_id: int
    product_code: str
    status: str
    amount: int
    term_months: int
    purpose: str
    purpose_details: str | None
    investment_object: str | None
    collateral_type: str | None
    collateral_value: int | None
    info_request: str | None
    info_response: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def status_label(self) -> str:
        labels = {
            'draft': 'Черновик',
            'submitted': 'Заявка отправлена',
            'checking': 'На рассмотрении',
            'scoring': 'На рассмотрении',
            'decision': 'На рассмотрении',
            'needs_info': 'Нужны дополнительные сведения',
            'manual_review': 'Ручное рассмотрение',
            'approved': 'Одобрено',
            'counter_offer': 'Встречное предложение',
            'accepted': 'Предложение принято',
            'signed_demo': 'Подписано (демо)',
            'disbursed_demo': 'Кредит выдан (демо)',
            'rejected': 'Отказано',
            'closed': 'Закрыта',
        }
        return labels[self.status]


class StatusEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    from_status: str | None
    to_status: str
    actor_role: str
    actor_user_id: int | None
    reason: str | None
    created_at: datetime


class OfferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: Literal['original', 'counter', 'alternative_product']
    product_code: str
    amount: int
    term_months: int
    rate: Decimal
    monthly_payment: int
    status: str
    created_at: datetime
