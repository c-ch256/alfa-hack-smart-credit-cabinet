"""Демонстрационный договор из условий Offer и неизменяемый снимок после принятия."""

from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application_service import own_application
from app.config import DEMO_BANK_NAME
from app.errors import ApiError
from app.models import Application, Company, CreditContract, Offer, StatusEvent, User
from app.services.pricing import payment_schedule
from app.services.staff import own_staff_application

NOTICE = 'ДЕМОНСТРАЦИОННЫЙ ДОГОВОР. НЕ ЯВЛЯЕТСЯ ЮРИДИЧЕСКИ ЗНАЧИМЫМ ДОКУМЕНТОМ'
BANK = {
    'name': DEMO_BANK_NAME,
    'inn': '0000000000',
    'ogrn': '0000000000000',
    'settlement_account': '00000000000000000000',
    'bik': '000000000',
    'correspondent_account': '00000000000000000000',
    'legal_address': 'Демо-адрес банка, вымышленный',
}


def _next_month(day: date) -> date:
    month = day.month % 12 + 1
    year = day.year + (day.month == 12)
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _borrower(company: Company) -> dict[str, str | None]:
    fields = (
        'registration_number',
        'legal_address',
        'settlement_account',
        'bank_bik',
        'bank_name',
    )
    if any(not getattr(company, name) for name in fields):
        raise ApiError(422, 'INCOMPLETE_REQUISITES', 'В профиле компании не хватает реквизитов')
    return {
        'legal_form': company.legal_form,
        'name': company.name,
        'inn': company.inn,
        'ogrn': company.registration_number if company.legal_form == 'OOO' else None,
        'ogrnip': company.registration_number if company.legal_form == 'IP' else None,
        'legal_address': company.legal_address,
        'settlement_account': company.settlement_account,
        'bank_bik': company.bank_bik,
        'bank_name': company.bank_name,
        'correspondent_account': company.correspondent_account,
    }


def build_contract(offer: Offer, application: Application, *, on_date: date) -> dict:
    schedule = payment_schedule(
        offer.amount, Decimal(offer.rate), offer.term_months, _next_month(on_date)
    )
    total = sum(row['payment_rub'] for row in schedule)
    return {
        'notice': NOTICE,
        'number': f'DEMO-{offer.id:08d}',
        'contract_date': on_date.isoformat(),
        'offer_id': offer.id,
        'application_id': application.id,
        'bank': BANK.copy(),
        'borrower': _borrower(application.company),
        'product_code': offer.product_code,
        'product_name': offer.product.name,
        'purpose': application.purpose,
        'amount_rub': offer.amount,
        'annual_rate_percent': str(offer.rate),
        'term_months': offer.term_months,
        'monthly_payment_rub': offer.monthly_payment,
        'total_payments_rub': total,
        'overpayment_rub': total - offer.amount,
        'repayment_method': 'Аннуитет; проценты на остаток; последний платёж корректирующий',
        'collateral_type': application.collateral_type
        if offer.product_code == 'investment'
        else None,
        'collateral_value_rub': application.collateral_value
        if offer.product_code == 'investment'
        else None,
        'schedule_url': f'/offers/{offer.id}/schedule',
        'schedule': schedule,
    }


def accessible_offer(session: Session, offer_id: int, actor: User) -> tuple[Offer, Application]:
    offer = session.get(Offer, offer_id)
    if offer is None:
        raise ApiError(404, 'NOT_FOUND', 'Предложение не найдено')
    application = (
        own_application(session, offer.application_id, actor)
        if actor.role == 'CLIENT'
        else own_staff_application(session, offer.application_id, actor)
    )
    if offer.status == 'proposed':
        latest_id = session.scalar(
            select(Offer.id)
            .where(Offer.application_id == application.id)
            .order_by(Offer.id.desc())
            .limit(1)
        )
        if application.status not in ('approved', 'counter_offer') or latest_id != offer.id:
            raise ApiError(404, 'NOT_FOUND', 'Предложение не найдено')
    elif offer.status != 'accepted' or application.status not in (
        'accepted',
        'signed_demo',
        'disbursed_demo',
    ):
        raise ApiError(404, 'NOT_FOUND', 'Предложение не найдено')
    return offer, application


def contract_data(session: Session, offer: Offer, application: Application) -> dict:
    if offer.status == 'proposed':
        terms = build_contract(offer, application, on_date=datetime.now(UTC).date())
        return terms | {'signing_status': 'preview', 'fixed': False}
    # Сериализует первое открытие старого принятого предложения между запросами PostgreSQL.
    session.execute(
        select(Application.id).where(Application.id == application.id).with_for_update()
    ).scalar_one()
    snapshot = session.scalar(select(CreditContract).where(CreditContract.offer_id == offer.id))
    if snapshot is None:
        # Старые принятые предложения: сохраняем ставку из Offer и дату фактического принятия.
        accepted_event = session.scalar(
            select(StatusEvent)
            .where(
                StatusEvent.application_id == application.id,
                StatusEvent.to_status == 'accepted',
            )
            .order_by(StatusEvent.id.desc())
            .limit(1)
        )
        accepted_at = accepted_event.created_at if accepted_event is not None else offer.created_at
        on_date = (
            accepted_at.astimezone(UTC).date()
            if accepted_at.tzinfo is not None
            else accepted_at.date()
        )
        snapshot = CreditContract(
            offer_id=offer.id,
            application_id=application.id,
            terms=build_contract(offer, application, on_date=on_date),
        )
        session.add(snapshot)
        session.flush()
    status = (
        'signed_demo' if application.status in ('signed_demo', 'disbursed_demo') else 'accepted'
    )
    return snapshot.terms | {'signing_status': status, 'fixed': True}
