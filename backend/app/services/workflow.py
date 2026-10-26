"""Атомарный синхронный конвейер демонстрационной заявки."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application_schemas import AdditionalInfo
from app.application_service import own_application, transition
from app.config import Settings
from app.errors import ApiError
from app.models import (
    Application,
    ApplicationSnapshot,
    AuditEvent,
    CheckResult,
    Company,
    Decision,
    Offer,
    Product,
    Score,
    StatusEvent,
    User,
)
from app.scoring.base import (
    FEATURE_SCHEMA_VERSION,
    CompanyFeatures,
    Scorer,
    ScoringRequest,
    ScoringResult,
)
from app.scoring.selector import ScorerUnavailableError, select_scorer
from app.services.additional_info_history import INFO_FIELDS, active_info_request
from app.services.checks import CheckOutcome, run_checks
from app.services.mocks import bki_by_inn, fns_by_inn
from app.services.policy import (
    AssessmentUnavailableError,
    PolicyResult,
    build_scoring_request,
    evaluate_policy,
    precheck_policy,
)

MAX_TECHNICAL_RETRIES = 3  # Демонстрационное ограничение дорогой операции.


def _months_since(start: date, today: date) -> int:
    return max(
        0, (today.year - start.year) * 12 + today.month - start.month - (today.day < start.day)
    )


def _snapshot_data(application: Application, company: Company, today: date) -> dict:
    features = CompanyFeatures(
        legal_form=company.legal_form,
        okved=company.okved,
        region=company.region,
        business_age_months=_months_since(company.registered_at, today),
        bank_client_months=_months_since(company.bank_client_since, today),
        avg_monthly_revenue=company.avg_monthly_revenue,
        avg_monthly_expenses=company.avg_monthly_expenses,
        revenue_trend_6m=company.revenue_trend_6m,
        existing_monthly_payments=company.existing_monthly_payments,
    )
    fields = (
        'company_id',
        'product_code',
        'amount',
        'term_months',
        'purpose',
        'purpose_details',
        'investment_object',
        'collateral_type',
        'collateral_value',
        'info_response',
    )
    return {
        **{name: getattr(application, name) for name in fields},
        'company': features.model_dump(mode='json'),
        'company_profile': {
            'inn': company.inn,
            'name': company.name,
            'industry': company.industry,
            'registered_at': company.registered_at.isoformat(),
            'bank_client_since': company.bank_client_since.isoformat(),
        },
        'as_of': today.isoformat(),
    }


def _technical_audit(session: Session, application: Application, phase: str) -> None:
    session.add(
        AuditEvent(
            actor_user_id=None,
            actor_role='SYSTEM',
            company_id=application.company_id,
            application_id=application.id,
            object_type='application',
            object_id=str(application.id),
            action='workflow.technical_error',
            result='error',
            correlation_id=uuid4().hex,
            details={'phase': phase},
        )
    )
    session.flush()


def _policy_audit(
    session: Session,
    application: Application,
    result: PolicyResult,
    decision: Decision | None = None,
    offer: Offer | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_user_id=None,
            actor_role='SYSTEM',
            company_id=application.company_id,
            application_id=application.id,
            object_type='application',
            object_id=str(application.id),
            action='workflow.policy',
            result='success',
            correlation_id=uuid4().hex,
            rule_version=result.rule_version,
            details={
                'status': application.status,
                'decision': result.decision,
                'decision_id': decision.id if decision else None,
                'offer_id': offer.id if offer else None,
                'reason_codes': list(result.reason_codes),
            },
        )
    )


def _latest_snapshot(session: Session, application_id: int) -> ApplicationSnapshot:
    snapshot = session.scalar(
        select(ApplicationSnapshot)
        .where(ApplicationSnapshot.application_id == application_id)
        .order_by(ApplicationSnapshot.version.desc())
        .limit(1)
    )
    if snapshot is None:
        raise ApiError(409, 'INCONSISTENT_STATE', 'Снимок заявки не найден')
    return snapshot


def _latest_checks(session: Session, snapshot_id: int) -> dict[str, CheckOutcome]:
    rows = session.scalars(
        select(CheckResult)
        .where(CheckResult.snapshot_id == snapshot_id)
        .order_by(CheckResult.id.desc())
    )
    outcomes = {}
    for row in rows:
        outcomes.setdefault(row.source, CheckOutcome(row.status, row.summary, row.payload))
    if set(outcomes) != {'DATA', 'FNS', 'BKI'}:
        raise AssessmentUnavailableError('check set incomplete')
    return outcomes


class RecordingScorer:
    """Сохраняет каждую попытку без изменения контракта Scorer."""

    def __init__(
        self,
        session: Session,
        application: Application,
        snapshot: ApplicationSnapshot,
        delegate: Scorer,
    ) -> None:
        self.session = session
        self.application = application
        self.snapshot = snapshot
        self.delegate = delegate
        self.rows: dict[tuple[str, int, int, int], Score] = {}
        self.results: dict[tuple[str, int, int, int], ScoringResult] = {}

    def score(self, req: ScoringRequest):
        source = getattr(self.delegate, 'source', 'model')
        is_test = getattr(self.delegate, 'is_test_result', False)
        if source not in ('mock', 'model') or type(is_test) is not bool:
            raise AssessmentUnavailableError('invalid scorer metadata')
        key = (req.product_code, req.amount, req.term_months, req.collateral_value)
        if key in self.results:
            return self.results[key]
        input_data = req.model_dump(mode='json')
        try:
            result = self.delegate.score(req)
            if not isinstance(result, ScoringResult):
                raise ValueError('invalid scorer result')
        except Exception:
            row = Score(
                application_id=self.application.id,
                snapshot_id=self.snapshot.id,
                source=source,
                is_test_result=is_test,
                result_status='error',
                pd=None,
                risk_band=None,
                model_version=getattr(self.delegate, 'model_version', 'unavailable'),
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                reasons=[],
                features={},
                input_snapshot=input_data,
                error_code='SCORER_FAILED',
            )
            self.session.add(row)
            self.session.flush()
            raise
        row = Score(
            application_id=self.application.id,
            snapshot_id=self.snapshot.id,
            source=source,
            is_test_result=is_test,
            result_status='success',
            pd=result.pd,
            risk_band=result.risk_band,
            model_version=result.model_version,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            reasons=[reason.model_dump(mode='json') for reason in result.reasons],
            features={},
            input_snapshot=input_data,
            error_code=None,
        )
        self.session.add(row)
        self.session.flush()
        self.rows[key] = row
        self.results[key] = result
        return result


def _record_decision(
    session: Session,
    application: Application,
    snapshot: ApplicationSnapshot,
    result: PolicyResult,
    recorder: RecordingScorer | None,
) -> tuple[Decision | None, Offer | None]:
    if result.decision not in ('approve', 'counter', 'reject'):
        return None, None
    score = None
    if recorder is not None:
        if result.offer is not None:
            offer = result.offer
            collateral = (
                snapshot.data.get('collateral_value') or 0
                if (offer.product_code == 'investment')
                else 0
            )
            score = recorder.rows.get(
                (offer.product_code, offer.amount, offer.term_months, collateral)
            )
        else:
            score = recorder.rows.get(
                (
                    application.product_code,
                    application.amount,
                    application.term_months,
                    snapshot.data.get('collateral_value') or 0,
                )
            )
        if score is None:
            raise AssessmentUnavailableError('decision score unavailable')
    decision = Decision(
        application_id=application.id,
        snapshot_id=snapshot.id,
        score_id=score.id if score else None,
        decision=result.decision,
        created_by='SYSTEM',
        created_by_user_id=None,
        rule_version=result.rule_version,
        rationale=list(result.rationale),
    )
    session.add(decision)
    session.flush()
    offer_row = None
    if result.offer is not None:
        offered = result.offer
        if offered.product_code not in ('working_capital', 'investment'):
            raise AssessmentUnavailableError('offer product unavailable')
        offer_row = Offer(
            application_id=application.id,
            decision_id=decision.id,
            kind=offered.kind,
            product_code=offered.product_code,
            amount=offered.amount,
            term_months=offered.term_months,
            rate=offered.rate,
            monthly_payment=offered.monthly_payment,
            created_by='SYSTEM',
        )
        session.add(offer_row)
        session.flush()
    return decision, offer_row


def _claim_retry(session: Session, application: Application) -> None:
    failures = (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.application_id == application.id,
                AuditEvent.action == 'workflow.technical_error',
            )
        )
        or 0
    )
    if failures == 0:
        raise ApiError(409, 'INVALID_TRANSITION', 'Заявка уже обрабатывается')
    if failures > MAX_TECHNICAL_RETRIES:
        raise ApiError(503, 'PROCESSING_UNAVAILABLE', 'Обработка временно недоступна')
    changed = session.execute(
        update(Application)
        .where(
            Application.id == application.id,
            Application.status == application.status,
            Application.version == application.version,
        )
        .values(version=application.version + 1)
    )
    if changed.rowcount != 1:
        raise ApiError(409, 'CONCURRENT_UPDATE', 'Заявка была изменена')
    session.flush()


def submit_and_process(
    session: Session,
    application_id: int,
    actor: User,
    settings: Settings,
    *,
    scorer: Scorer | None = None,
    fns_provider: Callable[[str], dict] | None = None,
    bki_provider: Callable[[str], dict] | None = None,
) -> Application:
    """Вызывающий коммитит единую транзакцию после возврата."""
    application = own_application(session, application_id, actor)
    if application.status == 'draft':
        transition(session, application, 'submitted', actor, 'Клиент отправил заявку')
        submission_id = session.scalar(
            select(StatusEvent.id)
            .where(
                StatusEvent.application_id == application.id, StatusEvent.to_status == 'submitted'
            )
            .order_by(StatusEvent.sequence)
            .limit(1)
        )
        company = session.get(Company, application.company_id)
        snapshot = ApplicationSnapshot(
            application_id=application.id,
            version=application.version,
            data={
                **_snapshot_data(application, company, datetime.now(UTC).date()),
                'initial_submission_event_id': submission_id,
            },
        )
        session.add(snapshot)
        session.flush()
        transition(session, application, 'checking', None, 'Начата проверка заявки')
    elif application.status in ('checking', 'scoring'):
        _claim_retry(session, application)
        snapshot = _latest_snapshot(session, application.id)
    else:
        raise ApiError(409, 'INVALID_TRANSITION', 'Заявка уже отправлена')

    return process_application(
        session,
        application,
        snapshot,
        settings,
        scorer=scorer,
        fns_provider=fns_provider,
        bki_provider=bki_provider,
    )


def add_information_and_process(
    session: Session,
    application_id: int,
    actor: User,
    payload: AdditionalInfo,
    settings: Settings,
    *,
    scorer: Scorer | None = None,
    fns_provider: Callable[[str], dict] | None = None,
    bki_provider: Callable[[str], dict] | None = None,
) -> Application:
    application = own_application(session, application_id, actor)
    if application.status != 'needs_info':
        raise ApiError(
            409, 'INVALID_TRANSITION', 'Дополнить можно только ожидающую сведения заявку'
        )
    if application.version != payload.version:
        raise ApiError(409, 'CONCURRENT_UPDATE', 'Заявка была изменена')
    changed = payload.model_dump(exclude_unset=True, exclude={'version', 'request_event_id'})
    request_event_id, requested_fields = active_info_request(session, application.id)
    if payload.request_event_id is not None and payload.request_event_id != request_event_id:
        raise ApiError(409, 'CONCURRENT_UPDATE', 'Запрос сведений изменился')
    if (
        payload.request_event_id is not None
        and requested_fields is not None
        and not set(changed) <= set(requested_fields)
    ):
        raise ApiError(422, 'INVALID_INFO_FIELDS', 'Передайте только запрошенные сведения')
    changes = []
    for name, value in changed.items():
        before = getattr(application, 'info_response' if name == 'message' else name)
        if before != value:
            changes.append({'field': name, 'before': before, 'after': value})
    transition(session, application, 'checking', actor, 'Клиент дослал сведения')
    session.flush()
    response_event = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.application_id == application.id,
            AuditEvent.action == 'application.checking',
            AuditEvent.actor_user_id == actor.id,
            AuditEvent.actor_role == 'CLIENT',
        )
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    response_event.details = {
        **response_event.details,
        'additional_info': {
            'request_event_id': request_event_id,
            'response_event_id': response_event.id,
            'answered_at': response_event.created_at.isoformat(),
            'requested_fields': requested_fields,
            'changes': changes,
        },
    }
    for name, value in changed.items():
        setattr(application, 'info_response' if name == 'message' else name, value)
    application.info_request = None
    session.flush()
    company = session.get(Company, application.company_id)
    snapshot = ApplicationSnapshot(
        application_id=application.id,
        version=application.version,
        data=_snapshot_data(application, company, datetime.now(UTC).date()),
    )
    session.add(snapshot)
    session.flush()
    return process_application(
        session,
        application,
        snapshot,
        settings,
        scorer=scorer,
        fns_provider=fns_provider,
        bki_provider=bki_provider,
    )


def process_application(
    session: Session,
    application: Application,
    snapshot: ApplicationSnapshot,
    settings: Settings,
    *,
    scorer: Scorer | None = None,
    fns_provider: Callable[[str], dict] | None = None,
    bki_provider: Callable[[str], dict] | None = None,
) -> Application:
    """Продолжает уже разрешённый переход в checking/scoring без смены FSM."""
    if application.status not in ('checking', 'scoring'):
        raise ApiError(409, 'INVALID_TRANSITION', 'Переход статуса недопустим')
    products = {
        product.code: product
        for product in session.scalars(select(Product).where(Product.is_active.is_(True)))
    }
    try:
        delegate = select_scorer(
            settings, scorer, {code: product.margin_pp for code, product in products.items()}
        )
    except ScorerUnavailableError:
        _technical_audit(session, application, 'scorer_configuration')
        return application
    company_features = CompanyFeatures.model_validate(snapshot.data['company'])
    if application.status == 'checking':
        rows = run_checks(
            session,
            application,
            snapshot,
            fns_provider=fns_provider or fns_by_inn,
            bki_provider=bki_provider or bki_by_inn,
        )
        checks = {row.source: CheckOutcome(row.status, row.summary, row.payload) for row in rows}
        try:
            preliminary = precheck_policy(snapshot.data, checks, products)
        except AssessmentUnavailableError:
            _technical_audit(session, application, 'checking')
            return application
        if preliminary is not None and preliminary.decision in ('reject', 'needs_info'):
            if preliminary.decision == 'needs_info':
                application.info_request = preliminary.rationale[0]
                transition(
                    session,
                    application,
                    'needs_info',
                    None,
                    'Требуются дополнительные сведения',
                    audit_details={
                        'requested_fields': [
                            name
                            for name in checks['DATA'].payload.get('missing_fields', [])
                            if name in INFO_FIELDS
                        ]
                    },
                )
            else:
                decision, offer = _record_decision(
                    session, application, snapshot, preliminary, None
                )
                transition(
                    session, application, 'rejected', None, 'Решение по демонстрационным правилам'
                )
            _policy_audit(
                session,
                application,
                preliminary,
                decision if preliminary.decision == 'reject' else None,
                offer if preliminary.decision == 'reject' else None,
            )
            return application
        transition(session, application, 'scoring', None, 'Начата оценка заявки')
    else:
        checks = _latest_checks(session, snapshot.id)
        preliminary = precheck_policy(snapshot.data, checks, products)

    recorder = RecordingScorer(session, application, snapshot, delegate)
    try:
        if preliminary is not None and preliminary.decision == 'manual_review':
            request = build_scoring_request(snapshot.data, company_features, checks)
            try:
                recorder.score(request)
            except SQLAlchemyError:
                raise
            except Exception as exc:
                raise AssessmentUnavailableError('scoring failed') from exc
            result = preliminary
        else:
            result = evaluate_policy(
                snapshot.data,
                company_features,
                checks,
                products,
                recorder,
                settings.key_rate,
            )
    except AssessmentUnavailableError:
        _technical_audit(session, application, 'scoring')
        return application
    if result.decision not in ('approve', 'counter', 'manual_review', 'reject'):
        raise AssessmentUnavailableError('unexpected decision after scoring')
    if result.decision == 'manual_review':
        staff_id = next(
            (
                user.id
                for user in session.scalars(
                    select(User).where(User.role == 'STAFF').order_by(User.id)
                )
                if user.is_active
            ),
            None,
        )
        if staff_id is None:
            _technical_audit(session, application, 'staff_assignment')
            return application
        application.assigned_staff_user_id = staff_id
        session.flush()
    transition(session, application, 'decision', None, 'Оценка заявки завершена')
    decision, offer = _record_decision(session, application, snapshot, result, recorder)
    destination = {
        'approve': 'approved',
        'counter': 'counter_offer',
        'manual_review': 'manual_review',
        'reject': 'rejected',
    }[result.decision]
    transition(session, application, destination, None, 'Решение по демонстрационным правилам')
    _policy_audit(session, application, result, decision, offer)
    return application
