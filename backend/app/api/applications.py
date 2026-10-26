from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application_schemas import (
    AdditionalInfo,
    ApplicationCreate,
    ApplicationPatch,
    ApplicationResponse,
    ClientApplicationDetailResponse,
    CompanyResponse,
    ClientProductResponse,
    OfferResponse,
    ProductResponse,
    StatusEventResponse,
)
from app.application_service import (
    create_application,
    edit_application,
    own_application,
    own_company,
    transition,
)
from app.contract_schemas import CompanyRequisites
from app.deps import get_session, require_role
from app.models import Application, Company, Offer, Product, StatusEvent, User
from app.services.additional_info_history import active_info_request
from app.services.contracts import _borrower
from app.services.pricing import RISK_PREMIUM_PP, annual_rate
from app.services.workflow import add_information_and_process, submit_and_process

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


@router.get('/me/companies/{company_id}/requisites', response_model=CompanyRequisites)
async def company_requisites(
    company_id: Annotated[int, Path(gt=0, le=2_147_483_647)], user: Client, session: Db
) -> dict:
    return _borrower(own_company(session, company_id, user)) | {'demonstration': True}


@router.get('/products', response_model=list[ClientProductResponse])
async def list_products(
    _user: Client, session: Db, request: Request
) -> list[ClientProductResponse]:
    minimum_band = min(RISK_PREMIUM_PP, key=RISK_PREMIUM_PP.get)
    return [
        ClientProductResponse(
            **ProductResponse.model_validate(product).model_dump(),
            indicative_rate_min=annual_rate(
                request.app.state.settings.key_rate, product.margin_pp, minimum_band
            ),
        )
        for product in session.scalars(
            select(Product).where(Product.is_active.is_(True)).order_by(Product.code)
        )
    ]


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


@router.get('/applications/{application_id}', response_model=ClientApplicationDetailResponse)
async def get_application(
    application_id: ApplicationId, user: Client, session: Db
) -> ClientApplicationDetailResponse:
    application = own_application(session, application_id, user)
    request_id, fields = (
        active_info_request(session, application_id)
        if application.status == 'needs_info' else (None, None)
    )
    return ClientApplicationDetailResponse(
        **ApplicationResponse.model_validate(application).model_dump(),
        active_info_request={'request_event_id': request_id, 'fields': fields}
        if request_id is not None and fields else None,
    )


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
    application_id: ApplicationId, user: Client, session: Db, request: Request
) -> Application:
    application = submit_and_process(
        session,
        application_id,
        user,
        request.app.state.settings,
        scorer=getattr(request.app.state, 'workflow_scorer', None),
        fns_provider=getattr(request.app.state, 'fns_provider', None),
        bki_provider=getattr(request.app.state, 'bki_provider', None),
    )
    session.commit()
    return application


@router.post('/applications/{application_id}/additional-info', response_model=ApplicationResponse)
async def add_application_information(
    application_id: ApplicationId,
    payload: AdditionalInfo,
    user: Client,
    session: Db,
    request: Request,
) -> Application:
    application = add_information_and_process(
        session,
        application_id,
        user,
        payload,
        request.app.state.settings,
        scorer=getattr(request.app.state, 'workflow_scorer', None),
        fns_provider=getattr(request.app.state, 'fns_provider', None),
        bki_provider=getattr(request.app.state, 'bki_provider', None),
    )
    session.commit()
    return application


@router.get('/applications/{application_id}/offers', response_model=list[OfferResponse])
async def application_offers(
    application_id: ApplicationId, user: Client, session: Db
) -> list[Offer]:
    own_application(session, application_id, user)
    return list(
        session.scalars(
            select(Offer)
            .where(Offer.application_id == application_id)
            .order_by(Offer.created_at, Offer.id)
        )
    )


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
