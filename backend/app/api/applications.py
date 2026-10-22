from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application_schemas import (
    ApplicationCreate,
    ApplicationPatch,
    ApplicationResponse,
    CompanyResponse,
    ProductResponse,
    StatusEventResponse,
)
from app.application_service import (
    create_application,
    edit_application,
    own_application,
    transition,
)
from app.deps import get_session, require_role
from app.models import Application, Company, Product, StatusEvent, User

router = APIRouter(tags=['Клиентские заявки'])
Client = Annotated[User, Depends(require_role('CLIENT'))]
Db = Annotated[Session, Depends(get_session)]
ApplicationId = Annotated[int, Path(gt=0, le=2_147_483_647)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.get('/me/companies', response_model=list[CompanyResponse])
async def list_companies(user: Client, session: Db) -> list[Company]:
    return list(
        session.scalars(
            select(Company).where(Company.owner_user_id == user.id).order_by(Company.id)
        )
    )


@router.get('/products', response_model=list[ProductResponse])
async def list_products(_user: Client, session: Db) -> list[Product]:
    return list(
        session.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.code))
    )


@router.post('/applications', response_model=ApplicationResponse, status_code=201)
async def create_draft(
    payload: ApplicationCreate,
    response: Response,
    user: Client,
    session: Db,
    idempotency_key: Annotated[
        str,
        Header(
            alias='Idempotency-Key', min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._~-]+$'
        ),
    ],
) -> Application:
    application, created = create_application(session, payload, idempotency_key, user)
    if created:
        session.commit()
    else:
        response.status_code = 200
    response.headers['Cache-Control'] = 'no-store'
    return application


@router.get('/applications', response_model=list[ApplicationResponse])
async def list_applications(
    user: Client, session: Db, limit: PageLimit = 50, offset: PageOffset = 0
) -> list[Application]:
    return list(
        session.scalars(
            select(Application)
            .join(Company, Application.company_id == Company.id)
            .where(Company.owner_user_id == user.id)
            .order_by(Application.created_at.desc(), Application.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


@router.get('/applications/{application_id}', response_model=ApplicationResponse)
async def get_application(application_id: ApplicationId, user: Client, session: Db) -> Application:
    return own_application(session, application_id, user)


@router.patch('/applications/{application_id}', response_model=ApplicationResponse)
async def patch_application(
    application_id: ApplicationId, payload: ApplicationPatch, user: Client, session: Db
) -> Application:
    application = own_application(session, application_id, user)
    application = edit_application(session, application, payload, user)
    session.commit()
    return application


@router.post('/applications/{application_id}/submit', response_model=ApplicationResponse)
async def submit_application(
    application_id: ApplicationId, user: Client, session: Db
) -> Application:
    application = own_application(session, application_id, user)
    application = transition(session, application, 'submitted', user, 'Клиент отправил заявку')
    session.commit()
    return application


@router.get('/applications/{application_id}/history', response_model=list[StatusEventResponse])
async def application_history(
    application_id: ApplicationId,
    user: Client,
    session: Db,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
) -> list[StatusEvent]:
    own_application(session, application_id, user)
    return list(
        session.scalars(
            select(StatusEvent)
            .where(StatusEvent.application_id == application_id)
            .order_by(StatusEvent.sequence)
            .limit(limit)
            .offset(offset)
        )
    )


@router.post('/applications/{application_id}/withdraw', response_model=ApplicationResponse)
async def withdraw_application(
    application_id: ApplicationId, user: Client, session: Db
) -> Application:
    application = own_application(session, application_id, user)
    application = transition(session, application, 'closed', user, 'Клиент отозвал заявку')
    session.commit()
    return application
