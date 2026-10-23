from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application_schemas import ApplicationCreate, ApplicationPatch
from app.errors import ApiError
from app.models import (
    Application,
    ApplicationSnapshot,
    AuditEvent,
    Company,
    Product,
    StatusEvent,
    User,
)

# Единственная таблица допустимых переходов по docs/00_common.md, раздел 8.
TRANSITIONS: dict[tuple[str, str], str] = {
    ('draft', 'submitted'): 'CLIENT',
    ('draft', 'closed'): 'CLIENT',
    ('needs_info', 'checking'): 'CLIENT',
    ('needs_info', 'closed'): 'CLIENT',
    ('approved', 'accepted'): 'CLIENT',
    ('approved', 'closed'): 'CLIENT',
    ('counter_offer', 'accepted'): 'CLIENT',
    ('counter_offer', 'closed'): 'CLIENT',
    ('accepted', 'signed_demo'): 'CLIENT',
    ('submitted', 'checking'): 'SYSTEM',
    ('checking', 'scoring'): 'SYSTEM',
    ('checking', 'needs_info'): 'SYSTEM',
    ('checking', 'rejected'): 'SYSTEM',
    ('scoring', 'decision'): 'SYSTEM',
    ('decision', 'approved'): 'SYSTEM',
    ('decision', 'counter_offer'): 'SYSTEM',
    ('decision', 'manual_review'): 'SYSTEM',
    ('decision', 'rejected'): 'SYSTEM',
    ('signed_demo', 'disbursed_demo'): 'SYSTEM',
    ('manual_review', 'approved'): 'STAFF',
    ('manual_review', 'counter_offer'): 'STAFF',
    ('manual_review', 'rejected'): 'STAFF',
    ('manual_review', 'needs_info'): 'STAFF',
}


def audit(session: Session, application: Application, actor: User | None, action: str) -> None:
    session.add(
        AuditEvent(
            actor_user_id=actor.id if actor else None,
            actor_role=actor.role if actor else 'SYSTEM',
            company_id=application.company_id,
            application_id=application.id,
            object_type='application',
            object_id=str(application.id),
            action=action,
            result='success',
            correlation_id=uuid4().hex,
            details={'status': application.status},
        )
    )


def own_company(session: Session, company_id: int, user: User) -> Company:
    company = session.scalar(
        select(Company).where(Company.id == company_id, Company.owner_user_id == user.id)
    )
    if company is None:
        raise ApiError(404, 'NOT_FOUND', 'Компания не найдена')
    return company


def own_application(session: Session, application_id: int, user: User) -> Application:
    application = session.scalar(
        select(Application)
        .join(Company, Application.company_id == Company.id)
        .where(Application.id == application_id, Company.owner_user_id == user.id)
    )
    if application is None:
        raise ApiError(404, 'NOT_FOUND', 'Заявка не найдена')
    return application


def validate_terms(session: Session, fields: dict) -> None:
    product = session.get(Product, fields['product_code'])
    if product is None or not product.is_active:
        raise ApiError(422, 'INVALID_PRODUCT', 'Продукт недоступен')
    if not product.min_amount <= fields['amount'] <= product.max_amount:
        raise ApiError(422, 'INVALID_AMOUNT', 'Сумма вне лимитов продукта')
    if not product.min_term_months <= fields['term_months'] <= product.max_term_months:
        raise ApiError(422, 'INVALID_TERM', 'Срок вне лимитов продукта')
    if not fields['purpose'].strip():
        raise ApiError(422, 'INVALID_PURPOSE', 'Укажите цель кредита')
    # Неполный инвестиционный черновик допустим: DATA запросит сведения при submit.
    if fields['product_code'] != 'investment' and any(
        fields.get(name) is not None
        for name in ('investment_object', 'collateral_type', 'collateral_value')
    ):
        raise ApiError(422, 'INVALID_PRODUCT_FIELDS', 'Поля инвестиций не относятся к продукту')


def transition(
    session: Session, application: Application, to_status: str, actor: User | None, reason: str
) -> Application:
    from_status = application.status
    actor_role = actor.role if actor else 'SYSTEM'
    if TRANSITIONS.get((from_status, to_status)) != actor_role:
        raise ApiError(409, 'INVALID_TRANSITION', 'Переход статуса недопустим')
    next_version = application.version + 1
    changed = session.execute(
        update(Application)
        .where(
            Application.id == application.id,
            Application.status == from_status,
            Application.version == application.version,
        )
        .values(status=to_status, version=next_version)
    )
    if changed.rowcount != 1:
        raise ApiError(409, 'CONCURRENT_UPDATE', 'Заявка была изменена')
    sequence = (
        session.scalar(
            select(func.max(StatusEvent.sequence)).where(
                StatusEvent.application_id == application.id
            )
        )
        or 0
    ) + 1
    session.add(
        StatusEvent(
            application_id=application.id,
            sequence=sequence,
            from_status=from_status,
            to_status=to_status,
            actor_role=actor_role,
            actor_user_id=actor.id if actor else None,
            reason=reason,
        )
    )
    session.flush()
    audit(session, application, actor, f'application.{to_status}')
    return application


def _initial_request(session: Session, application_id: int) -> dict | None:
    snapshot = session.scalar(
        select(ApplicationSnapshot).where(
            ApplicationSnapshot.application_id == application_id,
            ApplicationSnapshot.version == 1,
        )
    )
    return snapshot.data if snapshot else None


def create_application(
    session: Session, payload: ApplicationCreate, key: str, actor: User
) -> tuple[Application, bool]:
    original = payload.model_dump(mode='json')
    existing = session.scalar(
        select(Application).where(
            Application.created_by_user_id == actor.id, Application.idempotency_key == key
        )
    )
    if existing:
        if _initial_request(session, existing.id) != original:
            raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Ключ уже использован для другого запроса')
        return existing, False
    own_company(session, payload.company_id, actor)
    validate_terms(session, original)
    application = Application(
        number=f'CR-{uuid4().hex.upper()}',
        created_by_user_id=actor.id,
        idempotency_key=key,
        status='draft',
        version=1,
        **original,
    )
    try:
        with session.begin_nested():
            session.add(application)
            session.flush()
            session.add(
                ApplicationSnapshot(application_id=application.id, version=1, data=original)
            )
            session.add(
                StatusEvent(
                    application_id=application.id,
                    sequence=1,
                    from_status=None,
                    to_status='draft',
                    actor_role='CLIENT',
                    actor_user_id=actor.id,
                    reason='Создан черновик',
                )
            )
            audit(session, application, actor, 'application.create')
            session.flush()
    except IntegrityError:
        session.expire_all()
        existing = session.scalar(
            select(Application).where(
                Application.created_by_user_id == actor.id,
                Application.idempotency_key == key,
            )
        )
        if existing and _initial_request(session, existing.id) == original:
            return existing, False
        if existing:
            raise ApiError(
                409, 'IDEMPOTENCY_CONFLICT', 'Ключ уже использован для другого запроса'
            ) from None
        raise
    return application, True


def edit_application(
    session: Session, application: Application, payload: ApplicationPatch, actor: User
) -> Application:
    if application.status != 'draft':
        raise ApiError(409, 'INVALID_TRANSITION', 'Редактировать можно только черновик')
    changed = payload.model_dump(exclude_unset=True)
    fields = {
        name: getattr(application, name)
        for name in ApplicationCreate.model_fields
        if name != 'company_id'
    }
    fields.update(changed)
    validate_terms(session, fields)
    next_version = application.version + 1
    result = session.execute(
        update(Application)
        .where(
            Application.id == application.id,
            Application.version == application.version,
            Application.status == 'draft',
        )
        .values(**changed, version=next_version)
    )
    if result.rowcount != 1:
        raise ApiError(409, 'CONCURRENT_UPDATE', 'Заявка была изменена')
    session.add(
        ApplicationSnapshot(
            application_id=application.id,
            version=next_version,
            data={'company_id': application.company_id, **fields},
        )
    )
    session.flush()
    audit(session, application, actor, 'application.edit')
    return application
