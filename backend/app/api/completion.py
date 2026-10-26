from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session

from app.application_schemas import ApplicationResponse
from app.completion_schemas import DemoCodeResponse, DemoSignInput
from app.deps import get_session, require_role
from app.models import Application, User
from app.services.completion import (
    accept_offer,
    decline_offer,
    issue_demo_code,
    sign_and_disburse_demo,
)

router = APIRouter(tags=['Предложения и демо-подписание'])
Client = Annotated[User, Depends(require_role('CLIENT'))]
Db = Annotated[Session, Depends(get_session)]
OfferId = Annotated[int, Path(gt=0, le=2_147_483_647)]
ApplicationId = Annotated[int, Path(gt=0, le=2_147_483_647)]


@router.post('/offers/{offer_id}/accept', response_model=ApplicationResponse)
async def accept_client_offer(offer_id: OfferId, user: Client, session: Db) -> Application:
    application = accept_offer(session, offer_id, user)
    session.commit()
    return application


@router.post('/offers/{offer_id}/decline', response_model=ApplicationResponse)
async def decline_client_offer(offer_id: OfferId, user: Client, session: Db) -> Application:
    application = decline_offer(session, offer_id, user)
    session.commit()
    return application


@router.post('/applications/{application_id}/sign-demo/code', response_model=DemoCodeResponse)
async def create_demo_sign_code(
    application_id: ApplicationId,
    user: Client,
    session: Db,
    request: Request,
) -> DemoCodeResponse:
    code, expires_at = issue_demo_code(session, application_id, user, request.app.state.settings)
    session.commit()
    return DemoCodeResponse(code=code, expires_at=expires_at)


@router.post(
    '/applications/{application_id}/sign-demo',
    response_model=ApplicationResponse,
    responses={
        422: {'description': 'Неверный код: INVALID_CODE; истёкший код: CODE_EXPIRED'},
        429: {'description': 'Исчерпаны попытки: CODE_ATTEMPTS_EXCEEDED'},
    },
)
async def complete_demo_signing(
    application_id: ApplicationId,
    payload: DemoSignInput,
    user: Client,
    session: Db,
    request: Request,
) -> Application:
    outcome = sign_and_disburse_demo(
        session, application_id, user, request.app.state.settings, payload.code
    )
    session.commit()
    if outcome.error is not None:
        raise outcome.error
    return outcome.application
