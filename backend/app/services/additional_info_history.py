"""Historical additions stored with their response audit event in the same transaction."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApplicationSnapshot, AuditEvent, StatusEvent
from app.staff_schemas import StaffInfoResponse, StaffInitialRequest

INFO_FIELDS = frozenset(
    {'purpose_details', 'investment_object', 'collateral_type', 'collateral_value'}
)


def active_info_request(
    session: Session, application_id: int
) -> tuple[int | None, list[str] | None]:
    status = session.scalar(
        select(StatusEvent)
        .where(StatusEvent.application_id == application_id, StatusEvent.to_status == 'needs_info')
        .order_by(StatusEvent.sequence.desc())
        .limit(1)
    )
    if status is None:
        return None, None
    rows = [
        row
        for row in session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.application_id == application_id,
                AuditEvent.action.in_(('application.needs_info', 'staff.request_info')),
            )
            .order_by(AuditEvent.id)
        )
        if row.details.get('status_event_id') == status.id
    ]
    if not rows:
        return None, None
    request = next((row for row in rows if row.action == 'application.needs_info'), rows[0])
    fields = None
    for row in rows:
        saved = row.details.get('requested_fields')
        if isinstance(saved, list) and all(
            isinstance(key, str) and key in INFO_FIELDS for key in saved
        ):
            fields = list(saved)
    return request.id, fields


def saved_info_response(event: AuditEvent) -> StaffInfoResponse | None:
    if event.action != 'application.checking' or event.actor_role != 'CLIENT':
        return None
    saved = event.details.get('additional_info')
    if not isinstance(saved, dict) or saved.get('response_event_id') != event.id:
        return None
    try:
        return StaffInfoResponse.model_validate(saved)
    except ValidationError:
        return None


def info_responses(session: Session, application_id: int) -> list[StaffInfoResponse]:
    responses = []
    for event in session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.application_id == application_id,
            AuditEvent.action == 'application.checking',
            AuditEvent.actor_role == 'CLIENT',
        )
        .order_by(AuditEvent.id.desc())
    ):
        saved = saved_info_response(event)
        if saved is not None:
            responses.append(saved)
        elif event.result == 'success':
            # Only metadata is known for legacy replies. Never manufacture changes.
            responses.append(
                StaffInfoResponse(
                    request_event_id=None,
                    response_event_id=event.id,
                    answered_at=event.created_at,
                    requested_fields=None,
                    changes=None,
                )
            )
    return responses


def initial_request(
    snapshots: list[ApplicationSnapshot], history: list[StatusEvent]
) -> StaffInitialRequest | None:
    submission = min(
        (
            event for event in history
            if event.from_status == 'draft'
            and event.to_status == 'submitted' and event.actor_role == 'CLIENT'
        ),
        key=lambda event: event.sequence,
        default=None,
    )
    if submission is None:
        return None
    candidates = [
        item
        for item in snapshots
        if item.data.get('initial_submission_event_id') == submission.id
    ]
    if not candidates:
        # Before explicit links, draft edits saved every version without company
        # features. Submission saved the next version, in the submission transaction.
        drafts = [item for item in snapshots if 'company' not in item.data]
        if not drafts:
            return None
        version = max(item.version for item in drafts) + 1
        candidates = [
            item for item in snapshots
            if item.version == version and 'company' in item.data
            and item.created_at == submission.created_at
        ]
    if len(candidates) != 1:
        return None
    snapshot = candidates[0]
    fields = (
        'product_code', 'amount', 'term_months', 'purpose', 'purpose_details',
        'investment_object', 'collateral_type', 'collateral_value',
    )
    try:
        return StaffInitialRequest(
            snapshot_id=snapshot.id,
            snapshot_version=snapshot.version,
            submitted_at=submission.created_at,
            **{key: snapshot.data[key] for key in fields},
        )
    except (KeyError, ValidationError):
        return None
