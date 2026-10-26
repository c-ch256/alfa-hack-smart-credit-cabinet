"""Прогон тест-сценариев (13_test_scenarios.md) по HTTP против API команды.

Запуск (нужен httpx: pip install httpx):
  python docs/product/smoke_scenarios.py http://127.0.0.1:8000 <DEMO_PASSWORD>
  python docs/product/smoke_scenarios.py http://localhost:5173/api <DEMO_PASSWORD>   # через прокси фронтенда
Пароль можно передать и через переменную окружения DEMO_PASSWORD.
Нужна свежая база с демо-данными: сценарии создают заявки и в конце блокируют вход client4 на минуту (TC-19).
Выход 0 — все сценарии прошли.
"""

import os
import sys
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DEMO_PASSWORD", "")
results: list[tuple[str, bool, str]] = []


def check(case_id: str, title: str, ok: bool, details: str = "") -> None:
    results.append((case_id, bool(ok), f"{title}{' — ' + details if details else ''}"))


def main() -> int:
    if not PASSWORD:
        print("Укажите DEMO_PASSWORD вторым аргументом или в переменной окружения")
        return 2
    c = httpx.Client(base_url=BASE, timeout=15)

    def login(user: str) -> dict:
        r = c.post("/auth/login", json={"login": user, "password": PASSWORD})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def company(h: dict) -> int:
        return c.get("/me/companies", headers=h).json()[0]["id"]

    def create(h: dict, key: str | None = None, **body) -> httpx.Response:
        payload = {
            "company_id": company(h),
            "product_code": "working_capital",
            "amount": 3_000_000,
            "term_months": 24,
            "purpose": "Текущие расходы",
            **body,
        }
        return c.post(
            "/applications",
            json=payload,
            headers={**h, "Idempotency-Key": key or uuid.uuid4().hex},
        )

    def submit(h: dict, **body) -> dict:
        app = create(h, **body).json()
        return c.post(f"/applications/{app['id']}/submit", headers=h).json()

    def history(h: dict, app_id: int) -> list[str]:
        return [
            e["to_status"]
            for e in c.get(f"/applications/{app_id}/history", headers=h).json()
        ]

    def offer(h: dict, app_id: int) -> dict:
        offers = c.get(f"/applications/{app_id}/offers", headers=h).json()
        return offers[0] if offers else {}

    h1, h2, h3, h4, s = (
        login(u) for u in ("client1", "client2", "client3", "client4", "staff1")
    )

    # TC-01, TC-06, TC-21: одобрение, полный путь, договор и график
    t0 = time.perf_counter()
    a = submit(h1)
    took = time.perf_counter() - t0
    o = offer(h1, a["id"])
    check(
        "TC-01",
        "Одобрение client1",
        a["status"] == "approved",
        f"{o.get('amount')} ₽ / {o.get('term_months')} мес. / {o.get('rate')}% / {o.get('monthly_payment')} ₽; решение за {took:.2f} с",
    )
    c.post(f"/offers/{o['id']}/accept", headers=h1)
    contract = c.get(f"/offers/{o['id']}/contract", headers=h1)
    pdf = c.get(f"/offers/{o['id']}/contract.pdf", headers=h1)
    schedule = c.get(f"/offers/{o['id']}/schedule", headers=h1).json()
    check(
        "TC-21",
        "Договор, PDF и график",
        contract.status_code == 200
        and pdf.content[:4] == b"%PDF"
        and len(schedule.get("payments", [])) == o.get("term_months")
        and schedule["payments"][-1]["balance_rub"] == 0,
        f"«{contract.json().get('notice', '')[:40]}…», PDF {len(pdf.content)} байт, платежей {len(schedule.get('payments', []))}",
    )
    check(
        "TC-21b",
        "Чужой договор → 404",
        c.get(f"/offers/{o['id']}/contract", headers=h2).status_code == 404,
    )
    code = c.post(f"/applications/{a['id']}/sign-demo/code", headers=h1).json()["code"]
    done = c.post(
        f"/applications/{a['id']}/sign-demo", json={"code": code}, headers=h1
    ).json()
    hist = history(h1, a["id"])
    check(
        "TC-06",
        "Полный путь до выдачи",
        done["status"] == "disbursed_demo"
        and hist[-3:] == ["accepted", "signed_demo", "disbursed_demo"],
        " → ".join(hist),
    )

    # TC-02: встречное
    a = submit(h2, amount=5_000_000, term_months=12)
    o = offer(h2, a["id"])
    check(
        "TC-02",
        "Встречное client2",
        a["status"] == "counter_offer",
        f"{o.get('amount')} ₽ / {o.get('term_months')} мес. / {o.get('rate')}% / {o.get('monthly_payment')} ₽",
    )

    # TC-03, TC-08: ручное рассмотрение, решение сотрудника, аудит
    a3 = submit(h3, amount=4_000_000)
    check("TC-03", "Ручное рассмотрение client3", a3["status"] == "manual_review")
    c.get(f"/staff/applications/{a3['id']}", headers=s)
    no_comment = c.post(
        f"/staff/applications/{a3['id']}/decision",
        headers=s,
        json={"decision": "approve"},
    )
    check("TC-08b", "Решение без комментария → 422", no_comment.status_code == 422)
    r = c.post(
        f"/staff/applications/{a3['id']}/decision",
        headers=s,
        json={
            "decision": "counter",
            "amount": 2_000_000,
            "term_months": 24,
            "comment": "Снижаем лимит из-за просрочки",
        },
    )
    audit = {
        e["action"]
        for e in c.get(
            "/staff/audit", params={"application_id": a3["id"]}, headers=s
        ).json()
    }
    check(
        "TC-08",
        "Решение сотрудника и аудит",
        r.status_code == 200
        and r.json()["status"] == "counter_offer"
        and {"staff.view", "staff.decision"} <= audit,
        "staff.view, staff.decision в журнале",
    )
    reasons = " ".join(
        e.get("reason") or ""
        for e in c.get(f"/applications/{a3['id']}/history", headers=h3).json()
    )
    check(
        "TC-23",
        "Клиент не видит комментарий сотрудника (П12)",
        "просрочки" not in reasons,
    )

    # TC-04: отказ на проверке
    a = submit(h4, amount=500_000, term_months=12)
    check(
        "TC-04",
        "Отказ client4 на проверке",
        a["status"] == "rejected"
        and history(h4, a["id"])[-2:] == ["checking", "rejected"],
        " → ".join(history(h4, a["id"])),
    )

    # TC-05, TC-07: запрос сведений и повторная проверка
    a = submit(
        h1,
        product_code="investment",
        amount=8_000_000,
        term_months=60,
        purpose="Оборудование",
        investment_object="Линия фасовки",
    )
    ok5 = a["status"] == "needs_info"
    r = c.post(
        f"/applications/{a['id']}/additional-info",
        headers=h1,
        json={
            "version": a["version"],
            "collateral_type": "Оборудование",
            "collateral_value": 9_000_000,
        },
    )
    check(
        "TC-05",
        "Запрос сведений (инвест. 8 млн без залога)",
        ok5,
        a.get("info_request") or "",
    )
    check(
        "TC-07",
        "Повторная проверка после досылки",
        r.status_code == 200 and r.json()["status"] == "approved",
        " → ".join(history(h1, a["id"])),
    )

    # TC-22: отзыв из ручного рассмотрения (П10)
    a = submit(h3, amount=4_000_000)
    w = c.post(f"/applications/{a['id']}/withdraw", headers=h3)
    check(
        "TC-22",
        "Отзыв из ручного рассмотрения",
        a["status"] == "manual_review" and w.json().get("status") == "closed",
    )

    # TC-09…TC-12: права и ошибки
    foreign = submit(h1)
    check(
        "TC-09",
        "Чужая заявка → 404",
        c.get(f"/applications/{foreign['id']}", headers=h2).status_code == 404,
    )
    check(
        "TC-10",
        "Клиент на staff-эндпоинте → 403",
        c.get("/staff/applications", headers=h1).status_code == 403,
    )
    check(
        "TC-11",
        "Повторная отправка → 409",
        c.post(f"/applications/{foreign['id']}/submit", headers=h1).status_code == 409,
    )
    r = create(h1, amount=100)
    check(
        "TC-12",
        "Сумма вне лимитов → 422",
        r.status_code == 422,
        r.json().get("detail", ""),
    )
    r = create(h1, term_months=48)
    check(
        "TC-12b",
        "Срок вне лимитов → 422",
        r.status_code == 422,
        r.json().get("detail", ""),
    )

    # TC-13: двойной клик
    key = uuid.uuid4().hex
    before = len(c.get("/applications", headers=h2).json())
    r1, r2 = (
        create(h2, key=key, amount=1_000_000, term_months=12),
        create(h2, key=key, amount=1_000_000, term_months=12),
    )
    after = len(c.get("/applications", headers=h2).json())
    s1 = c.post(f"/applications/{r1.json()['id']}/submit", headers=h2).status_code
    s2 = c.post(f"/applications/{r1.json()['id']}/submit", headers=h2).status_code
    check(
        "TC-13",
        "Двойной клик не создаёт дубль",
        r1.json()["id"] == r2.json()["id"]
        and after == before + 1
        and (s1, s2) == (200, 409),
        f"создание {r1.status_code}/{r2.status_code}, отправка {s1}/{s2}",
    )

    # TC-14: черновик
    d = create(h2, amount=1_000_000, term_months=12).json()
    p = c.patch(f"/applications/{d['id']}", json={"amount": 2_000_000}, headers=h2)
    check(
        "TC-14",
        "Черновик сохраняется и редактируется",
        d["status"] == "draft" and p.json().get("amount") == 2_000_000,
    )

    # TC-15: продукты и предварительная ставка для калькулятора
    products = {p["code"]: p for p in c.get("/products", headers=h1).json()}
    check(
        "TC-15",
        "Продукты отдают лимиты и ставку «от»",
        {"working_capital", "investment"} <= set(products)
        and all("indicative_rate_min" in p for p in products.values()),
        ", ".join(
            f"{k}: от {v.get('indicative_rate_min')}%" for k, v in products.items()
        ),
    )

    # TC-16, TC-18: данные компании не вводятся; клиент не видит PD
    schema = c.get("/openapi.json").json()["components"]["schemas"]
    fields = set(schema.get("ApplicationCreate", {}).get("properties", {}))
    check(
        "TC-16",
        "Данные компании не вводятся вручную",
        fields and not fields & {"inn", "avg_monthly_revenue", "okved"},
        "поля анкеты: " + ", ".join(sorted(fields)),
    )
    app_view = c.get(f"/applications/{foreign['id']}", headers=h1).json()
    check(
        "TC-18",
        "Клиент не видит PD и группу риска",
        not {"pd", "score", "risk_band", "reasons"} & set(app_view),
    )

    # TC-20, TC-19: без токена; лимит попыток входа (последним)
    check("TC-20", "Без токена → 401", c.get("/applications").status_code == 401)
    codes = [
        c.post(
            "/auth/login", json={"login": "client4", "password": "wrong-password"}
        ).status_code
        for _ in range(6)
    ]
    # Лимит команды — 5 попыток в минуту на логин, включая успешный вход client4 выше.
    check(
        "TC-19",
        "Лимит попыток входа → 429",
        429 in codes
        and set(codes[: codes.index(429)]) == {401}
        and codes.index(429) >= 4,
        str(codes),
    )

    width = max(len(r[0]) for r in results)
    for case_id, ok, text in results:
        print(f"{'PASS' if ok else 'FAIL'}  {case_id:<{width}}  {text}")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} сценариев прошли")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
