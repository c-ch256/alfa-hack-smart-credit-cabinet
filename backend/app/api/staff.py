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
    Offer,
    Score,
    StatusEvent,
    User,
)
from app.services.staff import make_manual_decision, own_staff_application, request_information
from app.services.workflow import _latest_snapshot
from app.staff_schemas import (
    ManualDecisionInput,
    RequestInfoInput,
    StaffApplicationDetail,
    StaffAuditResponse,
    StaffCheckResponse,
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
    query = select(Application).where(Application.assigned_staff_user_id == staff.id)
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
    application_id: ApplicationId, staff: Staff, session: Db
) -> StaffApplicationDetail:
    application = own_staff_application(session, application_id, staff)
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
    )
    audit(session, application, staff, 'staff.view')
    session.commit()
    return detail


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
        application = own_staff_application(session, application_id, staff)
    query = (
        select(AuditEvent)
        .join(Application, AuditEvent.application_id == Application.id)
        .where(Application.assigned_staff_user_id == staff.id)
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
            details={'scope': 'application' if application else 'assigned'},
        )
    )
    session.commit()
    return rows
