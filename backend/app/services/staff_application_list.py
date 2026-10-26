"""Bulk STAFF list facts; never infer decision authors from application status."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application_schemas import ApplicationResponse
from app.models import Application, ApplicationSnapshot, Company, Decision, Offer, Score, StatusEvent
from app.staff_schemas import StaffApplicationListResponse, StaffWorkCategory


def work_categories(
    application: Application,
    decision: Decision | None,
    offers: list[Offer],
    history: list[StatusEvent],
) -> list[StaffWorkCategory]:
    status = application.status
    if status == 'manual_review':
        return ['manual_review']
    if status == 'needs_info':
        request = max(
            (event for event in history if event.to_status == 'needs_info'),
            key=lambda event: event.sequence,
            default=None,
        )
        return ['needs_info'] if request and request.actor_role == 'STAFF' else []
    if decision is None or decision.created_by != 'STAFF':
        return []
    linked = [
        offer
        for offer in offers
        if offer.decision_id == decision.id and offer.created_by == 'STAFF'
    ]
    if decision.decision == 'reject' and status in ('rejected', 'closed'):
        return ['staff_rejected']
    if (
        decision.decision == 'approve'
        and status in ('approved', 'accepted', 'signed_demo', 'disbursed_demo', 'closed')
        and any(offer.kind == 'original' for offer in linked)
    ):
        return ['staff_approved']
    changed_offers = [
        offer for offer in linked if offer.kind in ('counter', 'alternative_product')
    ]
    if decision.decision == 'counter':
        if status == 'counter_offer' and any(offer.status == 'proposed' for offer in changed_offers):
            return ['waiting_offer']
        if status in ('accepted', 'signed_demo', 'disbursed_demo') and any(
            offer.status == 'accepted' for offer in changed_offers
        ):
            return ['staff_offer_accepted']
    return []


def received_additional_info(status: str, history: list[StatusEvent]) -> bool:
    if status != 'manual_review':
        return False
    reply = max(
        (
            event.sequence
            for event in history
            if event.actor_role == 'CLIENT'
            and event.from_status == 'needs_info'
            and event.to_status == 'checking'
        ),
        default=None,
    )
    if reply is None:
        return False
    later = [event for event in history if event.sequence > reply]
    ended = {
        'approved', 'rejected', 'counter_offer', 'accepted', 'signed_demo',
        'disbursed_demo', 'closed', 'needs_info',
    }
    return any(event.to_status == 'manual_review' for event in later) and not any(
        event.to_status in ended for event in later
    )


def staff_list_rows(
    session: Session, applications: list[Application]
) -> list[StaffApplicationListResponse]:
    if not applications:
        return []
    ids = [application.id for application in applications]
    companies = {
        company.id: company
        for company in session.scalars(
            select(Company).where(Company.id.in_({row.company_id for row in applications}))
        )
    }
    decisions = {}
    for decision in session.scalars(
        select(Decision).where(Decision.application_id.in_(ids)).order_by(Decision.id.desc())
    ):
        decisions.setdefault(decision.application_id, decision)
    offers = {key: [] for key in ids}
    for offer in session.scalars(select(Offer).where(Offer.application_id.in_(ids))):
        offers[offer.application_id].append(offer)
    history = {key: [] for key in ids}
    for event in session.scalars(select(StatusEvent).where(StatusEvent.application_id.in_(ids))):
        history[event.application_id].append(event)
    latest_versions = (
        select(
            ApplicationSnapshot.application_id,
            func.max(ApplicationSnapshot.version).label('version'),
        )
        .where(ApplicationSnapshot.application_id.in_(ids))
        .group_by(ApplicationSnapshot.application_id)
        .subquery()
    )
    latest_snapshots = select(ApplicationSnapshot.id).join(
        latest_versions,
        (ApplicationSnapshot.application_id == latest_versions.c.application_id)
        & (ApplicationSnapshot.version == latest_versions.c.version),
    )
    scores = {}
    for score in session.scalars(
        select(Score).where(Score.snapshot_id.in_(latest_snapshots)).order_by(Score.id.desc())
    ):
        scores.setdefault(score.application_id, score)
    result = []
    for application in applications:
        company = companies[application.company_id]
        score = scores.get(application.id)
        result.append(
            StaffApplicationListResponse(
                **ApplicationResponse.model_validate(application).model_dump(),
                company={'name': company.name, 'inn': company.inn},
                risk_band=score.risk_band if score and score.result_status == 'success' else None,
                received_additional_info=received_additional_info(
                    application.status, history[application.id]
                ),
                work_categories=work_categories(
                    application, decisions.get(application.id), offers[application.id],
                    history[application.id],
                ),
            )
        )
    return result
