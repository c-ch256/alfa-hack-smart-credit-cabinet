"""Принятие предложения и локальная демонстрация подписания и выдачи."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.application_service import audit, own_application, transition, validate_terms
from app.config import Settings
from app.errors import ApiError
from app.models import Application, CreditContract, Offer, Product, User
from app.services.checks import check_data
from app.services.contracts import build_contract

CODE_TTL = timedelta(minutes=5)
MAX_CODE_ATTEMPTS = 5


def _owned_offer(session: Session, offer_id: int, actor: User) -> tuple[Offer, Application]:
    offer = session.get(Offer, offer_id)
    if offer is None:
        raise ApiError(404, 'NOT_FOUND', 'Предложение не найдено')
    application = own_application(session, offer.application_id, actor)
    return offer, application


def _current_proposal(session: Session, offer: Offer, application: Application) -> None:
    if application.status not in ('approved', 'counter_offer') or offer.status != 'proposed':
        raise ApiError(409, 'INVALID_TRANSITION', 'Предложение уже недоступно')
    latest_offer_id = session.scalar(
        select(Offer.id)
        .where(Offer.application_id == application.id)
        .order_by(Offer.id.desc())
        .limit(1)
    )
    if latest_offer_id != offer.id:
        raise ApiError(409, 'STALE_OFFER', 'Предложение уже неактуально')


def _change_offer_status(session: Session, offer: Offer, status: str) -> None:
    result = session.execute(
        update(Offer).where(Offer.id == offer.id, Offer.status == 'proposed').values(status=status)
    )
    if result.rowcount != 1:
        raise ApiError(409, 'CONCURRENT_UPDATE', 'Предложение было изменено')


def accept_offer(session: Session, offer_id: int, actor: User) -> Application:
    offer, application = _owned_offer(session, offer_id, actor)
    _current_proposal(session, offer, application)
    fields = {
        'company_id': application.company_id,
        'product_code': offer.product_code,
        'amount': offer.amount,
        'term_months': offer.term_months,
        'purpose': application.purpose,
        'investment_object': application.investment_object
        if offer.product_code == 'investment'
        else None,
        'collateral_type': application.collateral_type
        if offer.product_code == 'investment'
        else None,
        'collateral_value': application.collateral_value
        if offer.product_code == 'investment'
        else None,
    }
    validate_terms(session, fields)
    if check_data(fields, session.get(Product, offer.product_code)).status != 'ok':
        raise ApiError(422, 'MISSING_PRODUCT_DATA', 'Не хватает сведений для продукта')
    terms = build_contract(offer, application, on_date=datetime.now(UTC).date())
    transition(
        session,
        application,
        'accepted',
        actor,
        'Клиент принял предложение',
        audit_details={'offer_id': offer.id},
    )
    _change_offer_status(session, offer, 'accepted')
    session.add(CreditContract(offer_id=offer.id, application_id=application.id, terms=terms))
    audit(session, application, actor, 'offer.accept', {'offer_id': offer.id})
    session.flush()
    return application


def decline_offer(session: Session, offer_id: int, actor: User) -> Application:
    offer, application = _owned_offer(session, offer_id, actor)
    _current_proposal(session, offer, application)
    transition(
        session,
        application,
        'closed',
        actor,
        'Клиент отклонил предложение',
        audit_details={'offer_id': offer.id},
    )
    _change_offer_status(session, offer, 'declined')
    audit(session, application, actor, 'offer.decline', {'offer_id': offer.id})
    session.flush()
    return application


def _digest(settings: Settings, application_id: int, code: str) -> str:
    if settings.jwt_secret is None:
        raise ApiError(503, 'SIGNING_UNAVAILABLE', 'Демо-подписание недоступно')
    key = settings.jwt_secret.get_secret_value().encode('utf-8')
    message = f'demo-sign:v1:{application_id}:{code}'.encode('ascii')
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def issue_demo_code(
    session: Session,
    application_id: int,
    actor: User,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    application = own_application(session, application_id, actor)
    if application.status != 'accepted':
        raise ApiError(409, 'INVALID_TRANSITION', 'Сначала примите предложение')
    current = now or datetime.now(UTC)
    if application.sign_code_expires_at is not None and (
        _aware(application.sign_code_expires_at) > current
    ):
        raise ApiError(429, 'CODE_ALREADY_ISSUED', 'Код уже выдан; дождитесь окончания его срока')
    code = f'{secrets.randbelow(1_000_000):06d}'
    expires_at = current + CODE_TTL
    digest = _digest(settings, application.id, code)
    changed = session.execute(
        update(Application)
        .where(
            Application.id == application.id,
            Application.status == 'accepted',
            Application.version == application.version,
        )
        .values(
            sign_code_hash=digest,
            sign_code_expires_at=expires_at,
            sign_code_attempts=0,
            version=application.version + 1,
        )
    )
    if changed.rowcount != 1:
        raise ApiError(409, 'CONCURRENT_UPDATE', 'Заявка была изменена')
    audit(session, application, actor, 'sign_demo.code_issued')
    session.flush()
    return code, expires_at


@dataclass(frozen=True)
class SignAttempt:
    application: Application
    error: ApiError | None = None


def sign_and_disburse_demo(
    session: Session,
    application_id: int,
    actor: User,
    settings: Settings,
    code: str,
    *,
    now: datetime | None = None,
) -> SignAttempt:
    application = own_application(session, application_id, actor)
    if application.status != 'accepted':
        raise ApiError(409, 'INVALID_TRANSITION', 'Демо-подписание недоступно')
    current = now or datetime.now(UTC)
    if application.sign_code_attempts >= MAX_CODE_ATTEMPTS:
        raise ApiError(429, 'CODE_ATTEMPTS_EXCEEDED', 'Лимит попыток кода исчерпан')
    if application.sign_code_expires_at is None:
        raise ApiError(409, 'CODE_NOT_ISSUED', 'Запросите код демо-подписания')
    if _aware(application.sign_code_expires_at) <= current:
        raise ApiError(422, 'CODE_EXPIRED', 'Срок действия кода истёк')
    if application.sign_code_hash is None:
        raise ApiError(409, 'CODE_NOT_ISSUED', 'Запросите код демо-подписания')
    expected = _digest(settings, application.id, code)
    if not hmac.compare_digest(expected, application.sign_code_hash):
        attempts = application.sign_code_attempts + 1
        changed = session.execute(
            update(Application)
            .where(
                Application.id == application.id,
                Application.status == 'accepted',
                Application.version == application.version,
            )
            .values(
                sign_code_attempts=attempts,
                sign_code_hash=None
                if attempts >= MAX_CODE_ATTEMPTS
                else application.sign_code_hash,
                version=application.version + 1,
            )
        )
        if changed.rowcount != 1:
            raise ApiError(409, 'CONCURRENT_UPDATE', 'Заявка была изменена')
        audit(session, application, actor, 'sign_demo.invalid_code')
        session.flush()
        status = 429 if attempts >= MAX_CODE_ATTEMPTS else 422
        error_code = 'CODE_ATTEMPTS_EXCEEDED' if status == 429 else 'INVALID_CODE'
        detail = 'Лимит попыток кода исчерпан' if status == 429 else 'Неверный код'
        return SignAttempt(application, ApiError(status, error_code, detail))
    transition(session, application, 'signed_demo', actor, 'Демо-подписание завершено')
    application.sign_code_hash = None
    application.sign_code_expires_at = None
    application.sign_code_attempts = 0
    audit(session, application, actor, 'sign_demo.signed')
    transition(session, application, 'disbursed_demo', None, 'Кредит выдан (демо)')
    audit(session, application, None, 'disbursement.demo')
    session.flush()
    return SignAttempt(application)
