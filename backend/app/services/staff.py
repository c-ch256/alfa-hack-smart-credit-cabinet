"""Ручные действия только по назначенным заявкам."""

from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import exists, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application_service import TRANSITIONS, transition, validate_terms
from app.config import Settings
from app.errors import ApiError
from app.models import (
    Application,
    ApplicationSnapshot,
    AuditEvent,
    Decision,
    Offer,
    Product,
    Score,
    StaffComment,
    StatusEvent,
    User,
)
from app.scoring.base import CompanyFeatures, Scorer, ScoringResult
from app.scoring.selector import ScorerUnavailableError, select_scorer
from app.services.checks import check_data
from app.services.policy import PURPOSE_PRODUCTS, AssessmentUnavailableError, build_scoring_request
from app.services.pricing import RISK_PREMIUM_PP, annuity_payment, credit_terms, dscr
from app.services.workflow import RecordingScorer, _latest_checks, _latest_snapshot
from app.staff_schemas import (
    LoanPreviewInput,
    LoanPreviewResponse,
    ManualDecisionInput,
    ProductLimits,
    RequestInfoInput,
    StaffDscrResponse,
)

MANUAL_RULE_VERSION = 'manual-demo-2026-1'
REQUEST_FIELD_LABELS = {
    'purpose_details': 'подробности цели кредита',
    'investment_object': 'объект инвестиций',
    'collateral_type': 'вид залога',
    'collateral_value': 'стоимость залога',
}


def submitted_application():
    return exists().where(
        StatusEvent.application_id == Application.id,
        StatusEvent.to_status == 'submitted',
    )


def viewable_staff_application(session: Session, application_id: int) -> Application:
    application = session.scalar(
        select(Application).where(Application.id == application_id, submitted_application())
    )
    if application is None:
        raise ApiError(404, 'NOT_FOUND', 'Заявка не найдена')
    return application


def _assessed_fields(
    session: Session,
    application: Application,
    snapshot: ApplicationSnapshot,
    code: str,
    amount: int,
    term_months: int,
) -> tuple[Product, dict]:
    product = session.get(Product, code)
    if product is None or not product.is_active:
        raise ApiError(422, 'INVALID_PRODUCT', 'Продукт недоступен')
    source = snapshot.data
    if code != source['product_code']:
        allowed = PURPOSE_PRODUCTS.get(source['purpose'].strip().lower(), frozenset())
        if code not in allowed:
            raise ApiError(422, 'INVALID_PRODUCT', 'Продукт не подходит для цели заявки')
    fields = {
        'company_id': application.company_id,
        'product_code': code,
        'amount': amount,
        'term_months': term_months,
        'purpose': source['purpose'],
        'investment_object': source.get('investment_object') if code == 'investment' else None,
        'collateral_type': source.get('collateral_type') if code == 'investment' else None,
        'collateral_value': source.get('collateral_value') if code == 'investment' else None,
    }
    validate_terms(session, fields)
    if check_data(fields, product).status != 'ok':
        raise ApiError(422, 'MISSING_PRODUCT_DATA', 'Не хватает сведений для продукта')
    return product, fields


def loan_preview(
    session: Session,
    payload: LoanPreviewInput,
    settings: Settings,
    *,
    scorer: Scorer | None = None,
) -> LoanPreviewResponse:
    application = viewable_staff_application(session, payload.application_id)
    snapshot = _latest_snapshot(session, application.id)
    product, fields = _assessed_fields(
        session,
        application,
        snapshot,
        payload.product_code,
        payload.amount,
        payload.term_months,
    )
    try:
        checks = _latest_checks(session, snapshot.id)
        company = CompanyFeatures.model_validate(snapshot.data['company'])
        request = build_scoring_request(fields, company, checks)
    except (AssessmentUnavailableError, KeyError, ValidationError):
        raise ApiError(503, 'ASSESSMENT_UNAVAILABLE', 'Оценка временно недоступна') from None
    margins = {
        item.code: item.margin_pp
        for item in session.scalars(select(Product).where(Product.is_active.is_(True)))
    }
    try:
        delegate = select_scorer(settings, scorer, margins)
        score = delegate.score(request)
        source = getattr(delegate, 'source', None)
        is_test = getattr(delegate, 'is_test_result', None)
        if (
            not isinstance(score, ScoringResult)
            or source not in ('mock', 'model')
            or type(is_test) is not bool
        ):
            raise ValueError('invalid scorer response')
        terms = credit_terms(
            product, payload.amount, payload.term_months, settings.key_rate, score.risk_band
        )
        coverage = dscr(
            company.avg_monthly_revenue,
            company.avg_monthly_expenses,
            company.existing_monthly_payments,
            terms.monthly_payment,
        )
    except (ScorerUnavailableError, ValueError, TypeError, ArithmeticError):
        raise ApiError(503, 'SCORING_UNAVAILABLE', 'Оценка временно недоступна') from None
    except SQLAlchemyError:
        raise
    except Exception:
        raise ApiError(503, 'SCORING_UNAVAILABLE', 'Оценка временно недоступна') from None
    return LoanPreviewResponse(
        application_id=application.id,
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.version,
        product=product,
        amount=payload.amount,
        term_months=payload.term_months,
        risk_band=score.risk_band,
        key_rate=settings.key_rate,
        risk_premium_pp=RISK_PREMIUM_PP[score.risk_band],
        pd=score.pd,
        model_version=score.model_version,
        score_source=source,
        is_test_result=is_test,
        scoring_reasons=[reason.model_dump(mode='json') for reason in score.reasons],
        annual_rate=terms.rate,
        monthly_payment=terms.monthly_payment,
        dscr=coverage,
        total_payments=terms.total_payments,
        overpayment=terms.overpayment,
        applied_constraints=ProductLimits.model_validate(product),
        indicative=True,
        demonstration=True,
        explanation='Предварительная оценка по сохранённому снимку; не является предложением',
    )


def staff_dscr(
    snapshot: ApplicationSnapshot,
    scores: list[Score],
    offers: list[Offer],
    decisions: dict[int, Decision],
    snapshots: dict[int, ApplicationSnapshot],
) -> list[StaffDscrResponse]:
    """Показатели по сохранённым условиям и финансовому снимку каждого решения."""
    unavailable = 'Недостаточно сохранённых данных для расчёта DSCR'

    def calculate(
        source: ApplicationSnapshot,
        scope: str,
        offer_id: int | None,
        product_code: str | None,
        amount: int | None,
        term: int | None,
        rate: Decimal | None,
    ) -> StaffDscrResponse:
        value = None
        if rate is not None:
            try:
                company = source.data['company']
                value = dscr(
                    company['avg_monthly_revenue'],
                    company['avg_monthly_expenses'],
                    company['existing_monthly_payments'],
                    annuity_payment(amount, rate, term),
                )
            except (KeyError, TypeError, ValueError, ArithmeticError):
                pass
        return StaffDscrResponse(
            scope=scope,
            offer_id=offer_id,
            snapshot_version=source.version,
            product_code=product_code,
            amount=amount,
            term_months=term,
            rate=rate if value is not None else None,
            value=value,
            explanation=None if value is not None else unavailable,
        )

    original = snapshot.data
    original_rate = None
    for offer in offers:
        decision = decisions.get(offer.decision_id)
        if (
            decision is None
            or decision.created_by != 'SYSTEM'
            or decision.snapshot_id != snapshot.id
            or decision.score_id is None
            or offer.product_code != original.get('product_code')
        ):
            continue
        original_score = max(
            (
                score
                for score in scores
                if score.snapshot_id == snapshot.id
                and score.id <= decision.score_id
                and score.result_status == 'success'
                and score.input_snapshot.get('product_code') == original.get('product_code')
                and score.input_snapshot.get('amount') == original.get('amount')
                and score.input_snapshot.get('term_months') == original.get('term_months')
            ),
            key=lambda score: score.id,
            default=None,
        )
        offer_score = next((score for score in scores if score.id == decision.score_id), None)
        if (
            original_score is not None
            and offer_score is not None
            and offer_score.result_status == 'success'
            and offer_score.input_snapshot.get('product_code') == offer.product_code
            and offer_score.input_snapshot.get('amount') == offer.amount
            and offer_score.input_snapshot.get('term_months') == offer.term_months
            and original_score.risk_band in RISK_PREMIUM_PP
            and offer_score.risk_band in RISK_PREMIUM_PP
        ):
            original_rate = (
                offer.rate
                + RISK_PREMIUM_PP[original_score.risk_band]
                - RISK_PREMIUM_PP[offer_score.risk_band]
            )
            break
    results = [
        calculate(
            snapshot,
            'original_application',
            None,
            original.get('product_code'),
            original.get('amount'),
            original.get('term_months'),
            original_rate,
        )
    ]
    for offer in offers:
        decision = decisions.get(offer.decision_id)
        source = snapshots.get(decision.snapshot_id) if decision is not None else None
        if source is not None:
            results.append(
                calculate(
                    source,
                    'offer',
                    offer.id,
                    offer.product_code,
                    offer.amount,
                    offer.term_months,
                    offer.rate,
                )
            )
    return results


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
            rule_version=MANUAL_RULE_VERSION
            if action in ('staff.decision', 'staff.request_info')
            else None,
            details={'status': application.status, **details},
        )
    )


def record_staff_comment(
    session: Session,
    application: Application,
    actor: User,
    body: str,
    *,
    action: str = 'staff.comment.create',
    details: dict | None = None,
) -> StaffComment:
    comment = StaffComment(
        application_id=application.id,
        author_user_id=actor.id,
        body=body.strip(),
    )
    session.add(comment)
    session.flush()
    _staff_audit(
        session,
        application,
        actor,
        action,
        {'comment_id': comment.id, **(details or {})},
    )
    return comment


def request_information(
    session: Session, application_id: int, actor: User, payload: RequestInfoInput
) -> Application:
    application = own_staff_application(session, application_id, actor)
    if application.status != 'manual_review':
        raise ApiError(409, 'INVALID_TRANSITION', 'Запрос сведений недопустим')
    allowed = {'purpose_details'}
    if application.product_code == 'investment':
        allowed.update(('investment_object', 'collateral_type', 'collateral_value'))
    if not set(payload.fields) <= allowed:
        raise ApiError(422, 'INVALID_INFO_FIELDS', 'Выбранные поля не относятся к продукту')
    transition(session, application, 'needs_info', actor, 'Сотрудник запросил сведения')
    status_event_id = session.scalar(
        select(StatusEvent.id)
        .where(StatusEvent.application_id == application.id)
        .order_by(StatusEvent.sequence.desc())
        .limit(1)
    )
    application.info_request = (
        'Пожалуйста, дополните: '
        + ', '.join(REQUEST_FIELD_LABELS[field] for field in payload.fields)
        + '.'
    )
    application.info_response = None
    record_staff_comment(
        session,
        application,
        actor,
        payload.comment,
        action='staff.request_info',
        details={
            'status_event_id': status_event_id,
            'requested_fields': payload.fields,
        },
    )
    session.flush()
    return application


def _offer_fields(
    session: Session,
    application: Application,
    snapshot: ApplicationSnapshot,
    payload: ManualDecisionInput,
) -> tuple[Product, dict]:
    code = payload.product_code or application.product_code
    amount = payload.amount or application.amount
    term_months = payload.term_months or application.term_months
    if payload.decision == 'counter' and (
        code,
        amount,
        term_months,
    ) == (application.product_code, application.amount, application.term_months):
        raise ApiError(422, 'UNCHANGED_OFFER', 'Встречное предложение должно менять условия')
    return _assessed_fields(session, application, snapshot, code, amount, term_months)


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
        product, fields = _offer_fields(session, application, snapshot, payload)
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
        offer_row = Offer(
            application_id=application.id,
            decision_id=decision.id,
            kind='original'
            if payload.decision == 'approve'
            else ('alternative_product' if product.code != application.product_code else 'counter'),
            product_code=product.code,
            amount=fields['amount'],
            term_months=fields['term_months'],
            rate=terms.rate,
            monthly_payment=terms.monthly_payment,
            created_by='STAFF',
        )
        session.add(offer_row)
        session.flush()
    destination = {'approve': 'approved', 'counter': 'counter_offer', 'reject': 'rejected'}[
        payload.decision
    ]
    if TRANSITIONS.get((application.status, destination)) != 'STAFF':
        raise ApiError(409, 'INVALID_TRANSITION', 'Ручное решение недопустимо')
    transition(session, application, destination, actor, 'Решение сотрудника')
    status_event_id = session.scalar(
        select(StatusEvent.id)
        .where(StatusEvent.application_id == application.id)
        .order_by(StatusEvent.sequence.desc())
        .limit(1)
    )
    _staff_audit(
        session,
        application,
        actor,
        'staff.decision',
        {
            'decision_id': decision.id,
            'decision': payload.decision,
            'status_event_id': status_event_id,
            'offer_id': offer_row.id if offer is not None else None,
        },
    )
    session.flush()
    return application
