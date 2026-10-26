from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.application_schemas import (
    ApplicationResponse,
    CompanyResponse,
    OfferResponse,
    StatusEventResponse,
)
from app.application_service import TRANSITIONS, audit
from app.deps import get_session, require_role
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
    StaffComment,
    StatusEvent,
    User,
)
from app.services.additional_info_history import (
    info_responses,
    initial_request,
    saved_info_response,
)
from app.services.pricing import RISK_PREMIUM_PP
from app.services.staff import (
    allowed_staff_actions,
    loan_preview,
    make_manual_decision,
    original_request_score,
    record_staff_comment,
    request_information,
    staff_dscr,
    submitted_application,
    viewable_staff_application,
)
from app.services.staff_application_list import staff_list_rows
from app.services.staff_notifications import staff_notifications
from app.services.workflow import _latest_snapshot
from app.staff_schemas import (
    LoanPreviewInput,
    LoanPreviewResponse,
    ManualDecisionInput,
    RequestInfoInput,
    StaffApplicationDetail,
    StaffApplicationListResponse,
    StaffApplicationResponse,
    StaffAuditResponse,
    StaffCheckResponse,
    StaffCommentInput,
    StaffCommentResponse,
    StaffDecisionResponse,
    StaffNotification,
    StaffProductCatalog,
    StaffScoreResponse,
    StaffStatusEventResponse,
)

router = APIRouter(prefix='/staff', tags=['Сотрудник'])
Staff = Annotated[User, Depends(require_role('STAFF'))]
Db = Annotated[Session, Depends(get_session)]
ApplicationId = Annotated[int, Path(gt=0, le=2_147_483_647)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]
STATUSES = {status for pair in TRANSITIONS for status in pair}


def _request_status_event_id(session: Session, event: AuditEvent) -> int | None:
    saved_id = event.details.get('status_event_id')
    if type(saved_id) is int:
        return saved_id
    if event.action != 'staff.request_info':
        return None
    prior = list(
        session.scalars(
            select(AuditEvent.id)
            .where(
                AuditEvent.application_id == event.application_id,
                AuditEvent.action == 'application.needs_info',
                AuditEvent.id < event.id,
            )
            .order_by(AuditEvent.id)
        )
    )
    if not prior:
        return None
    status_events = list(
        session.scalars(
            select(StatusEvent.id)
            .where(
                StatusEvent.application_id == event.application_id,
                StatusEvent.to_status == 'needs_info',
            )
            .order_by(StatusEvent.sequence)
        )
    )
    return status_events[len(prior) - 1] if len(status_events) >= len(prior) else None


def _comment_responses(
    session: Session, application_id: int, comments: list[StaffComment]
) -> list[StaffCommentResponse]:
    if not comments:
        return []
    comment_ids = {comment.id for comment in comments}
    events = {
        row.details['comment_id']: row
        for row in session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.application_id == application_id,
                AuditEvent.action.in_(('staff.comment.create', 'staff.request_info')),
            )
            .order_by(AuditEvent.id)
        )
        if type(row.details.get('comment_id')) is int and row.details['comment_id'] in comment_ids
    }
    return [
        (
            StaffCommentResponse.model_validate(comment).model_copy(
                update={
                    'audit_event_id': events[comment.id].id,
                    'status_event_id': _request_status_event_id(session, events[comment.id]),
                }
            )
            if comment.id in events
            else StaffCommentResponse.model_validate(comment)
        )
        for comment in comments
    ]


@router.get('/notifications', response_model=list[StaffNotification])
async def list_staff_notifications(
    staff: Staff,
    session: Db,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[StaffNotification]:
    return staff_notifications(session, staff.id, limit, offset)


@router.get('/products', response_model=StaffProductCatalog)
async def staff_products(_staff: Staff, session: Db, request: Request) -> StaffProductCatalog:
    return StaffProductCatalog(
        products=list(
            session.scalars(
                select(Product).where(Product.is_active.is_(True)).order_by(Product.code)
            )
        ),
        key_rate=request.app.state.settings.key_rate,
        risk_premium_pp=RISK_PREMIUM_PP,
        rate_formula='key_rate + margin_pp + risk_premium_pp',
        demonstration=True,
    )


@router.post('/loan-preview', response_model=LoanPreviewResponse)
async def staff_loan_preview(
    payload: LoanPreviewInput, staff: Staff, session: Db, request: Request
) -> LoanPreviewResponse:
    result = loan_preview(
        session,
        payload,
        request.app.state.settings,
        scorer=getattr(request.app.state, 'workflow_scorer', None),
    )
    audit(session, session.get(Application, payload.application_id), staff, 'staff.loan_preview')
    session.commit()
    return result


@router.get('/applications', response_model=list[StaffApplicationListResponse])
async def list_staff_applications(
    staff: Staff,
    session: Db,
    status: str | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[StaffApplicationListResponse]:
    if status is not None and status not in STATUSES:
        raise ApiError(422, 'INVALID_STATUS', 'Неизвестный статус заявки')
    query = select(Application).where(submitted_application())
    if status is not None:
        query = query.where(Application.status == status)
    rows = list(
        session.scalars(
            query.order_by(Application.created_at.desc(), Application.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    result = staff_list_rows(session, rows)
    session.add(
        AuditEvent(
            actor_user_id=staff.id,
            actor_role='STAFF',
            company_id=None,
            application_id=None,
            object_type='application_list',
            object_id=None,
            action='staff.list_view',
            result='success',
            correlation_id=uuid4().hex,
            details={'status_filter': status, 'limit': limit, 'offset': offset},
        )
    )
    session.commit()
    return result


@router.get('/applications/{application_id}', response_model=StaffApplicationDetail)
async def get_staff_application(
    application_id: ApplicationId, staff: Staff, session: Db, request: Request
) -> StaffApplicationDetail:
    application = viewable_staff_application(session, application_id)
    snapshot = _latest_snapshot(session, application.id)
    checks = {}
    for row in session.scalars(
        select(CheckResult)
        .where(CheckResult.snapshot_id == snapshot.id)
        .order_by(CheckResult.id.desc())
    ):
        checks.setdefault(row.source, row)
    scores = list(session.scalars(select(Score).where(Score.application_id == application.id)))
    score = original_request_score(snapshot, scores)
    score_response = None
    if score is not None:
        input_data = score.input_snapshot
        score_response = StaffScoreResponse(
            id=score.id,
            snapshot_id=score.snapshot_id,
            source=score.source,
            result_status=score.result_status,
            is_test_result=score.is_test_result,
            pd=score.pd,
            risk_band=score.risk_band,
            model_version=score.model_version,
            reasons=score.reasons,
            error_code=score.error_code,
            product_code=input_data.get('product_code'),
            amount=input_data.get('amount'),
            term_months=input_data.get('term_months'),
            created_at=score.created_at,
        )
    offers = list(
        session.scalars(
            select(Offer).where(Offer.application_id == application.id).order_by(Offer.id)
        )
    )
    decisions = {
        decision.id: decision
        for decision in session.scalars(
            select(Decision).where(Decision.application_id == application.id)
        )
    }
    comments = list(
        session.scalars(
            select(StaffComment)
            .where(StaffComment.application_id == application.id)
            .order_by(StaffComment.id)
        )
    )
    snapshots = {
        item.id: item
        for item in session.scalars(
            select(ApplicationSnapshot).where(ApplicationSnapshot.application_id == application.id)
        )
    }
    events = list(
        session.scalars(
            select(StatusEvent)
            .where(StatusEvent.application_id == application.id)
            .order_by(StatusEvent.sequence)
        )
    )
    actor_names = {
        user.id: user.full_name
        for user in session.scalars(
            select(User).where(
                User.id.in_(
                    {event.actor_user_id for event in events if event.actor_user_id is not None}
                )
            )
        )
    }
    detail = StaffApplicationDetail(
        application=StaffApplicationResponse.model_validate(application),
        initial_request=initial_request(list(snapshots.values()), events),
        allowed_actions=allowed_staff_actions(application, staff),
        company=CompanyResponse.model_validate(application.company),
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.version,
        company_snapshot=snapshot.data.get('company') or {},
        checks=[StaffCheckResponse.model_validate(row) for row in checks.values()],
        score=score_response,
        dscr=staff_dscr(
            session, request.app.state.settings, snapshot, scores, offers, decisions, snapshots
        ),
        additional_info_responses=info_responses(session, application.id),
        offers=[OfferResponse.model_validate(offer) for offer in offers],
        history=[
            StaffStatusEventResponse(
                **StatusEventResponse.model_validate(event).model_dump(),
                actor_display_name=actor_names.get(event.actor_user_id),
            )
            for event in events
        ],
        decisions=[
            StaffDecisionResponse.model_validate(row).model_copy(
                update={
                    'internal_comment': (
                        row.rationale[0] if row.created_by == 'STAFF' and row.rationale else None
                    )
                }
            )
            for row in decisions.values()
        ],
        comments=_comment_responses(session, application.id, comments),
    )
    audit(session, application, staff, 'staff.view')
    session.commit()
    return detail


@router.get('/applications/{application_id}/comments', response_model=list[StaffCommentResponse])
async def list_staff_comments(
    application_id: ApplicationId,
    staff: Staff,
    session: Db,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[StaffCommentResponse]:
    application = viewable_staff_application(session, application_id)
    comments = list(
        session.scalars(
            select(StaffComment)
            .where(StaffComment.application_id == application.id)
            .order_by(StaffComment.id)
            .limit(limit)
            .offset(offset)
        )
    )
    audit(session, application, staff, 'staff.comments_view')
    session.commit()
    return _comment_responses(session, application.id, comments)


@router.post(
    '/applications/{application_id}/comments',
    response_model=StaffCommentResponse,
    status_code=201,
)
async def create_staff_comment(
    application_id: ApplicationId,
    payload: StaffCommentInput,
    staff: Staff,
    session: Db,
) -> StaffCommentResponse:
    application = viewable_staff_application(session, application_id)
    comment = record_staff_comment(session, application, staff, payload.body)
    session.commit()
    return _comment_responses(session, application.id, [comment])[0]


@router.post('/applications/{application_id}/decision', response_model=ApplicationResponse)
async def staff_decision(
    application_id: ApplicationId,
    payload: ManualDecisionInput,
    staff: Staff,
    session: Db,
    request: Request,
) -> Application:
    application = make_manual_decision(
        session,
        application_id,
        staff,
        payload,
        request.app.state.settings,
        scorer=getattr(request.app.state, 'workflow_scorer', None),
    )
    session.commit()
    return application


@router.post('/applications/{application_id}/request-info', response_model=ApplicationResponse)
async def staff_request_info(
    application_id: ApplicationId,
    payload: RequestInfoInput,
    staff: Staff,
    session: Db,
) -> Application:
    application = request_information(session, application_id, staff, payload)
    session.commit()
    return application


@router.get('/audit', response_model=list[StaffAuditResponse])
async def staff_audit(
    staff: Staff,
    session: Db,
    application_id: Annotated[int | None, Query(gt=0, le=2_147_483_647)] = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[StaffAuditResponse]:
    application = None
    if application_id is not None:
        application = viewable_staff_application(session, application_id)
    query = (
        select(AuditEvent)
        .outerjoin(Application, AuditEvent.application_id == Application.id)
        .where(
            or_(
                submitted_application(),
                AuditEvent.application_id.is_(None)
                & AuditEvent.action.in_(('staff.list_view', 'staff.audit_view')),
            )
        )
    )
    if application is not None:
        query = query.where(AuditEvent.application_id == application.id)
    rows = list(session.scalars(query.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)))
    application_ids = {row.application_id for row in rows if row.application_id is not None}
    historical_status = {}
    legacy_policy_decisions = {}
    if application_ids:
        timeline = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.application_id.in_(application_ids))
            .order_by(AuditEvent.id)
        )
        last_status = {}
        for event in timeline:
            status_at_event = event.details.get('status')
            if status_at_event is None and event.action.startswith('application.'):
                candidate = event.action.removeprefix('application.')
                if candidate in STATUSES:
                    status_at_event = candidate
            if status_at_event is not None:
                last_status[event.application_id] = status_at_event
            historical_status[event.id] = last_status.get(event.application_id)
        for app_id in application_ids:
            policy_events = list(
                session.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.application_id == app_id,
                        AuditEvent.action == 'workflow.policy',
                    )
                    .order_by(AuditEvent.id)
                )
            )
            decisions = list(
                session.scalars(
                    select(Decision)
                    .where(Decision.application_id == app_id, Decision.created_by == 'SYSTEM')
                    .order_by(Decision.id)
                )
            )
            decision_iter = iter(decisions)
            for event in policy_events:
                if event.details.get('decision') in ('approve', 'counter', 'reject'):
                    matching = next(decision_iter, None)
                    if matching is not None and matching.decision == event.details.get('decision'):
                        legacy_policy_decisions[event.id] = matching
    response = []
    for row in rows:
        comment = None
        decision_source = None
        decision = None
        note = None
        decision_id = row.details.get('decision_id')
        if type(decision_id) is int:
            found = session.get(Decision, decision_id)
            if found is not None and found.application_id == row.application_id:
                decision = found
        elif row.action == 'workflow.policy':
            decision = legacy_policy_decisions.get(row.id)
        if row.action in ('staff.comment.create', 'staff.request_info'):
            comment_id = row.details.get('comment_id')
            note = session.get(StaffComment, comment_id) if type(comment_id) is int else None
            if note is not None and note.application_id == row.application_id:
                comment = note.body
            else:
                note = None
        if decision is not None:
            decision_source = decision.created_by
            if decision.created_by == 'STAFF':
                comment = decision.rationale[0] if decision.rationale else None
        elif row.action == 'workflow.policy':
            decision_source = 'SYSTEM'
        offer_id = row.details.get('offer_id')
        if type(offer_id) is not int and decision is not None:
            offer_id = session.scalar(select(Offer.id).where(Offer.decision_id == decision.id))
        offer = session.get(Offer, offer_id) if type(offer_id) is int else None
        if offer is not None and offer.application_id != row.application_id:
            offer = None
        actor = session.get(User, row.actor_user_id) if row.actor_user_id else None
        company = session.get(Company, row.company_id) if row.company_id else None
        response.append(
            StaffAuditResponse.model_validate(row).model_copy(
                update={
                    'additional_info': saved_info_response(row),
                    'info_request_text': row.details.get('info_request_text'),
                    'assigned_staff_user_id': row.details.get('assigned_staff_user_id'),
                    'assigned_staff_display_name': row.details.get('assigned_staff_display_name'),
                    'decision_source': decision_source,
                    'internal_comment': comment,
                    'actor_display_name': actor.full_name if actor else 'Система',
                    'company_name': company.name if company else None,
                    'status_at_event': historical_status.get(row.id),
                    'decision_id': decision.id if decision else None,
                    'status_event_id': _request_status_event_id(session, row),
                    'comment_id': note.id if note else None,
                    'comment_author_user_id': (
                        note.author_user_id
                        if note
                        else decision.created_by_user_id
                        if comment and decision
                        else None
                    ),
                    'comment_created_at': (
                        note.created_at
                        if note
                        else decision.created_at
                        if comment and decision
                        else None
                    ),
                    'offer_id': offer.id if offer else None,
                    'offer_terms': (
                        {
                            'product_code': offer.product_code,
                            'amount': offer.amount,
                            'term_months': offer.term_months,
                            'rate': str(offer.rate),
                            'monthly_payment': offer.monthly_payment,
                        }
                        if offer
                        else None
                    ),
                }
            )
        )
    session.add(
        AuditEvent(
            actor_user_id=staff.id,
            actor_role='STAFF',
            company_id=application.company_id if application else None,
            application_id=application.id if application else None,
            object_type='audit',
            object_id=str(application.id) if application else None,
            action='staff.audit_view',
            result='success',
            correlation_id=uuid4().hex,
            details={'scope': 'application' if application else 'all_submitted'},
        )
    )
    session.commit()
    return response
