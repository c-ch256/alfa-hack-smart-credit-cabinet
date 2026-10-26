"""Recipient-scoped notifications from linked business events; no audit-view notices."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, AuditEvent, Company, Decision, Offer, StatusEvent
from app.services.additional_info_history import saved_info_response
from app.staff_schemas import StaffNotification


def staff_notifications(
    session: Session, staff_id: int, limit: int, offset: int
) -> list[StaffNotification]:
    actions = (
        'application.manual_review',
        'application.checking',
        'application.closed',
        'offer.accept',
        'offer.decline',
    )
    rows = session.execute(
        select(AuditEvent, Application.number, Company.name)
        .join(Application, Application.id == AuditEvent.application_id)
        .join(Company, Company.id == Application.company_id)
        .where(AuditEvent.action.in_(actions), AuditEvent.result == 'success')
        .order_by(AuditEvent.id)
    )
    assignments = {}
    notices = []
    for event, number, company_name in rows:
        recipient = None
        kind = None
        if event.action == 'application.manual_review':
            assigned = event.details.get('assigned_staff_user_id')
            # Re-entering review after a reply is not another assignment.
            if type(assigned) is int and assignments.get(event.application_id) != assigned:
                recipient, kind = assigned, 'assigned'
            assignments[event.application_id] = assigned
        elif event.actor_role == 'CLIENT':
            if event.action == 'application.checking':
                response = saved_info_response(event)
                request = (
                    session.get(AuditEvent, response.request_event_id)
                    if response and response.request_event_id
                    else None
                )
                if (
                    request is not None
                    and request.application_id == event.application_id
                    and request.id < event.id
                    and request.actor_role == 'STAFF'
                    and request.action in ('application.needs_info', 'staff.request_info')
                    and request.result == 'success'
                ):
                    recipient, kind = request.actor_user_id, 'additional_info'
            elif event.action in ('offer.accept', 'offer.decline'):
                offer_id = event.details.get('offer_id')
                offer = session.get(Offer, offer_id) if type(offer_id) is int else None
                decision = session.get(Decision, offer.decision_id) if offer else None
                if (
                    offer is not None
                    and decision is not None
                    and offer.application_id == event.application_id
                    and decision.application_id == event.application_id
                    and offer.kind in ('counter', 'alternative_product')
                    and offer.created_by == decision.created_by == 'STAFF'
                    and decision.decision == 'counter'
                ):
                    recipient = decision.created_by_user_id
                    kind = 'offer_accepted' if event.action == 'offer.accept' else 'offer_declined'
            elif event.action == 'application.closed':
                status_id = event.details.get('status_event_id')
                status = session.get(StatusEvent, status_id) if type(status_id) is int else None
                if (
                    status is not None
                    and status.application_id == event.application_id
                    and status.to_status == 'closed'
                    and status.actor_role == 'CLIENT'
                    and status.reason == 'Клиент отозвал заявку'
                ):
                    # Explicit historical assignment, or an earlier recorded assignment.
                    recipient = event.details.get(
                        'assigned_staff_user_id', assignments.get(event.application_id)
                    )
                    kind = 'withdrawn'
        if recipient == staff_id and kind:
            notices.append(
                StaffNotification(
                    id=event.id,
                    application_id=event.application_id,
                    application_number=number,
                    company_name=company_name,
                    kind=kind,
                    created_at=event.created_at,
                )
            )
    notices.sort(key=lambda notice: (notice.created_at, notice.id), reverse=True)
    return notices[offset : offset + limit]
