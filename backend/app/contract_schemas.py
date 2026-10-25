from pydantic import BaseModel


class BorrowerDetails(BaseModel):
    legal_form: str
    name: str
    inn: str
    ogrn: str | None
    ogrnip: str | None
    legal_address: str
    settlement_account: str
    bank_bik: str
    bank_name: str
    correspondent_account: str | None


class CompanyRequisites(BorrowerDetails):
    demonstration: bool = True


class BankDetails(BaseModel):
    name: str
    inn: str
    ogrn: str
    settlement_account: str
    bik: str
    correspondent_account: str
    legal_address: str


class PaymentRow(BaseModel):
    number: int
    payment_date: str
    payment_rub: int
    interest_rub: int
    principal_rub: int
    balance_rub: int


class ContractResponse(BaseModel):
    notice: str
    number: str
    contract_date: str
    offer_id: int
    application_id: int
    bank: BankDetails
    borrower: BorrowerDetails
    product_code: str
    product_name: str
    purpose: str
    amount_rub: int
    annual_rate_percent: str
    term_months: int
    monthly_payment_rub: int
    total_payments_rub: int
    overpayment_rub: int
    repayment_method: str
    collateral_type: str | None
    collateral_value_rub: int | None
    schedule_url: str
    schedule: list[PaymentRow]
    signing_status: str
    fixed: bool


class ScheduleResponse(BaseModel):
    offer_id: int
    amount_rub: int
    term_months: int
    annual_rate_percent: str
    monthly_payment_rub: int
    total_payments_rub: int
    overpayment_rub: int
    fixed: bool
    payments: list[PaymentRow]
