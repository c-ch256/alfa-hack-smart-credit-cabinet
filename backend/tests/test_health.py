import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import create_app


def test_health_and_swagger_are_available_locally() -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url='http://testserver'
        ) as client:
            response = await client.get('/health')
            assert response.status_code == 200
            assert response.json() == {'status': 'ok'}
            assert (await client.get('/docs')).status_code == 200

    asyncio.run(check())


def test_unknown_host_is_rejected() -> None:
    async def check() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url='http://untrusted.example'
        ) as client:
            assert (await client.get('/health')).status_code == 400

    asyncio.run(check())
