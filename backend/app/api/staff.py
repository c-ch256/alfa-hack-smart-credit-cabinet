from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select
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
    AuditEvent,
    CheckResult,
    Decision,
    Offer,
    Product,
    Score,
    StaffComment,
    StatusEvent,
    User,
)
from app.services.pricing import annual_rate, annuity_payment, dscr
from app.services.staff import (
    add_internal_comment,
    loan_preview,
    make_manual_decision,
    request_information,
    visible_staff_application,
)
from app.services.workflow import _latest_snapshot
from app.staff_schemas import (
    LoanPreviewInput,
    LoanPreviewResponse,
    ManualDecisionInput,
    RequestInfoInput,
    StaffApplicationDetail,
    StaffAuditResponse,
    StaffCheckResponse,
    StaffCommentInput,
    StaffCommentResponse,
    StaffScoreResponse,
)

router = APIRouter(prefix='/staff', tags=['Сотрудник'])
Staff = Annotated[User, Depends(require_role('STAFF'))]
Db = Annotated[Session, Depends(get_session)]
ApplicationId = Annotated[int, Path(gt=0, le=2_147_483_647)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]
STATUSES = {status for pair in TRANSITIONS for status in pair}


@router.get('/applications', response_model=list[ApplicationResponse])
async def list_staff_applications(
    staff: Staff,
    session: Db,
    status: str | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> list[Application]:
    if status is not None and status not in STATUSES:
        raise ApiError(422, 'INVALID_STATUS', 'Неизвестный статус заявки')
    query = select(Application).where(Application.status != 'draft')
    if status is not None:
        query = query.where(Application.status == status)
    return list(
        session.scalars(
            query.order_by(Application.created_at.desc(), Application.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


@router.get('/applications/{application_id}', response_model=StaffApplicationDetail)
async def get_staff_application(
    application_id: ApplicationId, staff: Staff, session: Db, request: Request
) -> StaffApplicationDetail:
    application = visible_staff_application(session, application_id)
    snapshot = _latest_snapshot(session, application.id)
    checks = {}
    for row in session.scalars(
        select(CheckResult)
        .where(CheckResult.snapshot_id == snapshot.id)
        .order_by(CheckResult.id.desc())
    ):
        checks.setdefault(row.source, row)
    score = session.scalar(
        select(Score).where(Score.snapshot_id == snapshot.id).order_by(Score.id.desc()).limit(1)
    )
    score_response = None
    if score is not None:
        input_data = score.input_snapshot
        rate = None
        payment = None
        coverage = None
        if score.result_status == 'success':
            matching_offer = session.scalar(
                select(Offer)
                .join(Decision, Offer.decision_id == Decision.id)
                .where(Decision.score_id == score.id)
                .order_by(Offer.id.desc())
                .limit(1)
            )
            if matching_offer is not None:
                rate = matching_offer.rate
                payment = matching_offer.monthly_payment
            elif all(
                input_data.get(key) is not None for key in ('product_code', 'amount', 'term_months')
            ):
                product = session.get(Product, input_data['product_code'])
                if product is not None:
                    rate = annual_rate(
                        request.app.state.settings.key_rate, product.margin_pp, score.risk_band
                    )
                    payment = annuity_payment(input_data['amount'], rate, input_data['term_months'])
            if payment is not None:
                company_data = snapshot.data['company']
                coverage = dscr(
                    company_data['avg_monthly_revenue'],
                    company_data['avg_monthly_expenses'],
                    company_data['existing_monthly_payments'],
                    payment,
                )
        score_response = StaffScoreResponse(
            id=score.id,
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
            snapshot_id=score.snapshot_id,
            rate=rate,
            monthly_payment=payment,
            dscr=coverage,
            created_at=score.created_at,
        )
    offers = list(
        session.scalars(
            select(Offer).where(Offer.application_id == application.id).order_by(Offer.id)
        )
    )
    events = list(
        session.scalars(
            select(StatusEvent)
            .where(StatusEvent.application_id == application.id)
            .order_by(StatusEvent.sequence)
        )
    )
    detail = StaffApplicationDetail(
        application=ApplicationResponse.model_validate(application),
        company=CompanyResponse.model_validate(application.company),
        snapshot_version=snapshot.version,
        company_snapshot=snapshot.data['company'],
        checks=[StaffCheckResponse.model_validate(row) for row in checks.values()],
        score=score_response,
        offers=[OfferResponse.model_validate(offer) for offer in offers],
        history=[StatusEventResponse.model_validate(event) for event in events],
        internal_comments=[
            StaffCommentResponse.model_validate(row)
            for row in session.scalars(
                select(StaffComment)
                .where(StaffComment.application_id == application.id)
                .order_by(StaffComment.id)
            )
        ],
    )
    audit(session, application, staff, 'staff.view')
    session.commit()
    return detail


@router.post('/loan-preview', response_model=LoanPreviewResponse)
async def staff_loan_preview(
    payload: LoanPreviewInput, staff: Staff, session: Db, request: Request
) -> LoanPreviewResponse:
    result = loan_preview(
        session,
        staff,
        payload,
        request.app.state.settings,
        scorer=getattr(request.app.state, 'workflow_scorer', None),
    )
    session.commit()
    return result


@router.get('/applications/{application_id}/comments', response_model=list[StaffCommentResponse])
async def list_staff_comments(
    application_id: ApplicationId, staff: Staff, session: Db
) -> list[StaffComment]:
    visible_staff_application(session, application_id)
    return list(
        session.scalars(
            select(StaffComment)
            .where(StaffComment.application_id == application_id)
            .order_by(StaffComment.id)
        )
    )


@router.post(
    '/applications/{application_id}/comments', response_model=StaffCommentResponse, status_code=201
)
async def create_staff_comment(
    application_id: ApplicationId, payload: StaffCommentInput, staff: Staff, session: Db
) -> StaffComment:
    row = add_internal_comment(session, application_id, staff, payload)
    session.commit()
    return row


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
) -> list[AuditEvent]:
    application = None
    if application_id is not None:
        application = visible_staff_application(session, application_id)
    query = (
        select(AuditEvent)
        .join(Application, AuditEvent.application_id == Application.id)
        .where(Application.status != 'draft')
    )
    if application is not None:
        query = query.where(AuditEvent.application_id == application.id)
    rows = list(session.scalars(query.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)))
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
            details={'scope': 'application' if application else 'submitted'},
        )
    )
    session.commit()
    return rows
