"""Ручные действия только по назначенным заявкам."""

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application_service import TRANSITIONS, transition, validate_terms
from app.config import Settings
from app.errors import ApiError
from app.models import Application, AuditEvent, Decision, Offer, Product, Score, StaffComment, User
from app.scoring.base import CompanyFeatures, Scorer
from app.scoring.selector import ScorerUnavailableError, select_scorer
from app.services.checks import check_data
from app.services.policy import PURPOSE_PRODUCTS, build_scoring_request
from app.services.pricing import credit_terms, dscr
from app.services.workflow import RecordingScorer, _latest_checks, _latest_snapshot
from app.staff_schemas import (
    LoanPreviewInput,
    LoanPreviewResponse,
    ManualDecisionInput,
    RequestInfoInput,
    StaffCommentInput,
)

MANUAL_RULE_VERSION = 'manual-demo-2026-1'


def own_staff_application(session: Session, application_id: int, actor: User) -> Application:
    application = session.scalar(
        select(Application).where(
            Application.id == application_id,
            Application.assigned_staff_user_id == actor.id,
        )
    )
    if application is None:
        raise ApiError(404, 'NOT_FOUND', 'Заявка не найдена')
    return application


def visible_staff_application(session: Session, application_id: int) -> Application:
    application = session.scalar(
        select(Application).where(Application.id == application_id, Application.status != 'draft')
    )
    if application is None:
        raise ApiError(404, 'NOT_FOUND', 'Заявка не найдена')
    return application


def _staff_audit(
    session: Session,
    application: Application,
    actor: User,
    action: str,
    details: dict,
) -> None:
    session.add(
        AuditEvent(
            actor_user_id=actor.id,
            actor_role='STAFF',
            company_id=application.company_id,
            application_id=application.id,
            object_type='application',
            object_id=str(application.id),
            action=action,
            result='success',
            correlation_id=uuid4().hex,
            rule_version=MANUAL_RULE_VERSION,
            details=details,
        )
    )


def request_information(
    session: Session, application_id: int, actor: User, payload: RequestInfoInput
) -> Application:
    application = own_staff_application(session, application_id, actor)
    if application.status != 'manual_review':
        raise ApiError(409, 'INVALID_TRANSITION', 'Запрос сведений недопустим')
    transition(session, application, 'needs_info', actor, 'Сотрудник запросил сведения')
    application.info_request = payload.message.strip()
    application.info_response = None
    _staff_audit(session, application, actor, 'staff.request_info', {'status': 'needs_info'})
    session.flush()
    return application


def _offer_fields(
    session: Session, application: Application, payload: ManualDecisionInput | LoanPreviewInput
) -> tuple[Product, dict]:
    code = payload.product_code or application.product_code
    product = session.get(Product, code)
    if product is None or not product.is_active:
        raise ApiError(422, 'INVALID_PRODUCT', 'Продукт недоступен')
    if code != application.product_code:
        allowed = PURPOSE_PRODUCTS.get(application.purpose.strip().lower(), frozenset())
        if code not in allowed:
            raise ApiError(422, 'INVALID_PRODUCT', 'Продукт не подходит для цели заявки')
    fields = {
        'company_id': application.company_id,
        'product_code': code,
        'amount': payload.amount or application.amount,
        'term_months': payload.term_months or application.term_months,
        'purpose': application.purpose,
        'investment_object': application.investment_object if code == 'investment' else None,
        'collateral_type': application.collateral_type if code == 'investment' else None,
        'collateral_value': application.collateral_value if code == 'investment' else None,
    }
    if (
        isinstance(payload, ManualDecisionInput)
        and payload.decision == 'counter'
        and (
            fields['product_code'],
            fields['amount'],
            fields['term_months'],
        )
        == (application.product_code, application.amount, application.term_months)
    ):
        raise ApiError(422, 'UNCHANGED_OFFER', 'Встречное предложение должно менять условия')
    validate_terms(session, fields)
    if check_data(fields, product).status != 'ok':
        raise ApiError(422, 'MISSING_PRODUCT_DATA', 'Не хватает сведений для продукта')
    return product, fields


def loan_preview(
    session: Session,
    actor: User,
    payload: LoanPreviewInput,
    settings: Settings,
    *,
    scorer: Scorer | None = None,
) -> LoanPreviewResponse:
    application = visible_staff_application(session, payload.application_id)
    snapshot = _latest_snapshot(session, application.id)
    product, fields = _offer_fields(session, application, payload)
    try:
        checks = _latest_checks(session, snapshot.id)
        company = CompanyFeatures.model_validate(snapshot.data['company'])
        request = build_scoring_request(fields, company, checks)
        products = {
            item.code: item.margin_pp
            for item in session.scalars(select(Product).where(Product.is_active.is_(True)))
        }
        delegate = select_scorer(settings, scorer, products)
        score = delegate.score(request)
        terms = credit_terms(
            product, fields['amount'], fields['term_months'], settings.key_rate, score.risk_band
        )
    except SQLAlchemyError:
        raise
    except Exception:
        raise ApiError(503, 'SCORING_UNAVAILABLE', 'Оценка временно недоступна') from None
    result = LoanPreviewResponse(
        application_id=application.id,
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.version,
        product_code=product.code,
        amount=fields['amount'],
        term_months=fields['term_months'],
        pd=score.pd,
        risk_band=score.risk_band,
        model_version=score.model_version,
        rate=terms.rate,
        monthly_payment=terms.monthly_payment,
        dscr=dscr(
            company.avg_monthly_revenue,
            company.avg_monthly_expenses,
            company.existing_monthly_payments,
            terms.monthly_payment,
        ),
    )
    _staff_audit(
        session,
        application,
        actor,
        'staff.loan_preview',
        {
            'snapshot_id': snapshot.id,
            'product_code': product.code,
            'amount': fields['amount'],
            'term_months': fields['term_months'],
        },
    )
    return result


def add_internal_comment(
    session: Session, application_id: int, actor: User, payload: StaffCommentInput
) -> StaffComment:
    application = visible_staff_application(session, application_id)
    row = StaffComment(
        application_id=application.id, author_user_id=actor.id, body=payload.body.strip()
    )
    session.add(row)
    session.flush()
    _staff_audit(session, application, actor, 'staff.comment', {'comment_id': row.id})
    return row


def make_manual_decision(
    session: Session,
    application_id: int,
    actor: User,
    payload: ManualDecisionInput,
    settings: Settings,
    *,
    scorer: Scorer | None = None,
) -> Application:
    application = own_staff_application(session, application_id, actor)
    if application.status != 'manual_review':
        raise ApiError(409, 'INVALID_TRANSITION', 'Ручное решение недопустимо')
    snapshot = _latest_snapshot(session, application.id)
    score_id = None
    offer = None
    if payload.decision != 'reject':
        product, fields = _offer_fields(session, application, payload)
        checks = _latest_checks(session, snapshot.id)
        company = CompanyFeatures.model_validate(snapshot.data['company'])
        request = build_scoring_request(fields, company, checks)
        products = {
            item.code: item.margin_pp
            for item in session.scalars(select(Product).where(Product.is_active.is_(True)))
        }
        try:
            delegate = select_scorer(settings, scorer, products)
        except ScorerUnavailableError:
            raise ApiError(503, 'SCORING_UNAVAILABLE', 'Оценка временно недоступна') from None
        recorder = RecordingScorer(session, application, snapshot, delegate)
        try:
            score = recorder.score(request)
        except SQLAlchemyError:
            raise
        except Exception:
            raise ApiError(503, 'SCORING_UNAVAILABLE', 'Оценка временно недоступна') from None
        score_id = recorder.rows[
            (request.product_code, request.amount, request.term_months, request.collateral_value)
        ].id
        terms = credit_terms(
            product, fields['amount'], fields['term_months'], settings.key_rate, score.risk_band
        )
        offer = (product, fields, terms)
    else:
        previous = session.scalar(
            select(Score.id)
            .where(Score.snapshot_id == snapshot.id, Score.result_status == 'success')
            .order_by(Score.id.desc())
            .limit(1)
        )
        score_id = previous

    decision = Decision(
        application_id=application.id,
        snapshot_id=snapshot.id,
        score_id=score_id,
        decision=payload.decision,
        created_by='STAFF',
        created_by_user_id=actor.id,
        rule_version=MANUAL_RULE_VERSION,
        rationale=[payload.comment.strip()],
    )
    session.add(decision)
    session.flush()
    if offer is not None:
        product, fields, terms = offer
        session.execute(
            update(Offer)
            .where(Offer.application_id == application.id, Offer.status == 'proposed')
            .values(status='superseded')
        )
        session.add(
            Offer(
                application_id=application.id,
                decision_id=decision.id,
                kind='original'
                if payload.decision == 'approve'
                else (
                    'alternative_product' if product.code != application.product_code else 'counter'
                ),
                product_code=product.code,
                amount=fields['amount'],
                term_months=fields['term_months'],
                rate=terms.rate,
                monthly_payment=terms.monthly_payment,
                created_by='STAFF',
            )
        )
    destination = {'approve': 'approved', 'counter': 'counter_offer', 'reject': 'rejected'}[
        payload.decision
    ]
    if TRANSITIONS.get((application.status, destination)) != 'STAFF':
        raise ApiError(409, 'INVALID_TRANSITION', 'Ручное решение недопустимо')
    transition(session, application, destination, actor, 'Решение сотрудника')
    _staff_audit(
        session,
        application,
        actor,
        'staff.decision',
        {
            'decision_id': decision.id,
            'decision': payload.decision,
        },
    )
    session.flush()
    return application
