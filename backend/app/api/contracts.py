from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.orm import Session

from app.contract_schemas import ContractResponse, ScheduleResponse
from app.deps import get_current_user, get_session
from app.models import User
from app.services.contract_pdf import render_contract_pdf
from app.services.contracts import accessible_offer, contract_data

router = APIRouter(prefix='/offers', tags=['Демонстрационные документы'])
Db = Annotated[Session, Depends(get_session)]
Actor = Annotated[User, Depends(get_current_user)]
OfferId = Annotated[int, Path(gt=0, le=2_147_483_647)]


def _document(session: Session, offer_id: int, user: User) -> dict:
    offer, application = accessible_offer(session, offer_id, user)
    data = contract_data(session, offer, application)
    session.commit()
    return data


@router.get('/{offer_id}/contract', response_model=ContractResponse)
async def view_contract(offer_id: OfferId, user: Actor, session: Db) -> dict:
    return _document(session, offer_id, user)


@router.get('/{offer_id}/contract.pdf', response_class=Response)
async def download_contract(offer_id: OfferId, user: Actor, session: Db) -> Response:
    data = _document(session, offer_id, user)
    return Response(
        render_contract_pdf(data),
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{data["number"]}.pdf"'},
    )


@router.get('/{offer_id}/schedule', response_model=ScheduleResponse)
async def view_schedule(offer_id: OfferId, user: Actor, session: Db) -> dict:
    data = _document(session, offer_id, user)
    return {
        key: data[key]
        for key in (
            'offer_id',
            'amount_rub',
            'term_months',
            'annual_rate_percent',
            'monthly_payment_rub',
            'total_payments_rub',
            'overpayment_rub',
            'fixed',
        )
    } | {'payments': data['schedule']}
