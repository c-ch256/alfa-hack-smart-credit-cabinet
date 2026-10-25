"""Прогон ключевых тест-сценариев (13_test_scenarios.md) по HTTP против запущенного API.

Запуск (нужен httpx: pip install httpx): python docs/product/smoke_scenarios.py [http://localhost:8000]
Нужна свежая база с демо-данными (сценарии создают заявки). Выход 0 — все сценарии прошли.
"""
import sys
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api"
PASSWORD = "alfa-demo-2026"
results: list[tuple[str, bool, str]] = []


def check(case_id: str, title: str, ok: bool, details: str = "") -> None:
    results.append((case_id, ok, f"{title}{' — ' + details if details else ''}"))


def login(c: httpx.Client, user: str) -> dict:
    r = c.post(f"{BASE}/auth/login", json={"login": user, "password": PASSWORD})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def company(c: httpx.Client, h: dict) -> int:
    return c.get(f"{BASE}/me/companies", headers=h).json()[0]["id"]


def create(c: httpx.Client, h: dict, **body) -> httpx.Response:
    payload = {"company_id": company(c, h), "product_code": "working_capital", "amount": 3_000_000,
               "term_months": 24, "purpose": "Закупка товаров и сырья", **body}
    return c.post(f"{BASE}/applications", json=payload, headers={**h, "Idempotency-Key": uuid.uuid4().hex})


def submit(c: httpx.Client, h: dict, **body) -> dict:
    app = create(c, h, **body).json()
    return c.post(f"{BASE}/applications/{app['id']}/submit", headers=h).json()


def history(c: httpx.Client, h: dict, app_id: int) -> list[str]:
    return [e["to_status"] for e in c.get(f"{BASE}/applications/{app_id}/history", headers=h).json()]


def main() -> int:
    with httpx.Client(timeout=10) as c:
        h1, h2, h3, h4, s = (login(c, u) for u in ("client1", "client2", "client3", "client4", "staff1"))

        # TC-01 + TC-06: одобрение и полный путь до выдачи
        t0 = time.perf_counter()
        a = submit(c, h1)
        decision_s = time.perf_counter() - t0
        check("TC-01", "Одобрение client1", a["status"] == "approved", f"решение за {decision_s:.2f} с")
        offer = a["offers"][0]
        c.post(f"{BASE}/offers/{offer['id']}/accept", headers=h1)
        code = c.post(f"{BASE}/applications/{a['id']}/sign-demo/code", headers=h1).json()["code"]
        done = c.post(f"{BASE}/applications/{a['id']}/sign-demo", json={"code": code}, headers=h1).json()
        hist = history(c, h1, a["id"])
        check("TC-06", "Полный путь до disbursed_demo", done["status"] == "disbursed_demo"
              and hist[-3:] == ["accepted", "signed_demo", "disbursed_demo"], " → ".join(hist))

        # TC-02: встречное
        a = submit(c, h2, amount=5_000_000, term_months=12)
        o = a["offers"][0] if a["offers"] else {}
        check("TC-02", "Встречное client2", a["status"] == "counter_offer",
              f"{o.get('amount')} ₽ / {o.get('term_months')} мес., платёж {o.get('monthly_payment')} ₽")

        # TC-03 + TC-08: ручное рассмотрение, решение сотрудника, аудит
        a = submit(c, h3, amount=4_000_000)
        check("TC-03", "Ручное рассмотрение client3", a["status"] == "manual_review", "; ".join(a["decision_reasons"]))
        c.get(f"{BASE}/staff/applications/{a['id']}", headers=s)
        r = c.post(f"{BASE}/staff/applications/{a['id']}/decision", headers=s,
                   json={"decision": "counter", "amount": 2_000_000, "term_months": 24, "comment": "Снижаем лимит"})
        audit = {e["action"] for e in c.get(f"{BASE}/staff/audit", params={"application_id": a["id"]}, headers=s).json()}
        check("TC-08", "Решение сотрудника и аудит", r.json()["status"] == "counter_offer"
              and {"staff.view", "staff.decision"} <= audit, ", ".join(sorted(audit)))

        # TC-04: отказ
        a = submit(c, h4, amount=500_000, term_months=12)
        check("TC-04", "Отказ client4", a["status"] == "rejected" and history(c, h4, a["id"])[-1] == "rejected",
              "; ".join(a["decision_reasons"]))

        # TC-05 + TC-07: запрос сведений и повторная проверка
        a = submit(c, h1, product_code="investment", amount=8_000_000, term_months=60,
                   purpose="Покупка оборудования", investment_object="Линия фасовки")
        ok5 = a["status"] == "needs_info"
        r = c.post(f"{BASE}/applications/{a['id']}/additional-info", headers=h1,
                   json={"message": "Залог — оборудование", "collateral_type": "Оборудование", "collateral_value": 9_000_000})
        check("TC-05", "Запрос сведений client1 (инвест. 8 млн)", ok5, a.get("info_request") or "")
        check("TC-07", "Повторная проверка после досылки", r.json()["status"] == "approved",
              " → ".join(history(c, h1, a["id"])))

        # TC-09..TC-12: безопасность и ошибки
        foreign = submit(c, h1)
        check("TC-09", "Чужая заявка → 404", c.get(f"{BASE}/applications/{foreign['id']}", headers=h2).status_code == 404)
        check("TC-10", "Клиент на staff-эндпоинте → 403", c.get(f"{BASE}/staff/applications", headers=h1).status_code == 403)
        r = c.post(f"{BASE}/applications/{foreign['id']}/submit", headers=h1)
        check("TC-11", "Недопустимый переход → 409", r.status_code == 409 and r.json()["code"] == "invalid_transition")
        r = create(c, h1, amount=100)
        check("TC-12", "Сумма вне лимитов → 422", r.status_code == 422, r.json()["detail"])
        r = create(c, h1, term_months=48)
        check("TC-12b", "Срок вне лимитов → 422", r.status_code == 422, r.json()["detail"])

        # TC-13: двойной клик «Отправить» — тот же Idempotency-Key
        key = uuid.uuid4().hex
        body = {"company_id": company(c, h2), "product_code": "working_capital", "amount": 1_000_000,
                "term_months": 12, "purpose": "Аренда и текущие расходы"}
        before = len(c.get(f"{BASE}/applications", headers=h2).json())
        r1 = c.post(f"{BASE}/applications", json=body, headers={**h2, "Idempotency-Key": key})
        r2 = c.post(f"{BASE}/applications", json=body, headers={**h2, "Idempotency-Key": key})
        after = len(c.get(f"{BASE}/applications", headers=h2).json())
        s1 = c.post(f"{BASE}/applications/{r1.json()['id']}/submit", headers=h2).status_code
        s2 = c.post(f"{BASE}/applications/{r1.json()['id']}/submit", headers=h2).status_code
        check("TC-13", "Двойной клик не создаёт дубль", r1.json()["id"] == r2.json()["id"] and after == before + 1
              and (s1, s2) == (200, 409), f"создание {r1.status_code}/{r2.status_code}, отправка {s1}/{s2}")

        # TC-14: черновик сохраняется и редактируется
        d = c.post(f"{BASE}/applications", json=body, headers=h2).json()
        p = c.patch(f"{BASE}/applications/{d['id']}", json={"amount": 2_000_000}, headers=h2)
        check("TC-14", "Черновик сохраняется и редактируется", d["status"] == "draft" and p.json()["amount"] == 2_000_000)

        # TC-15: подбор для «оборудования»
        rec = c.post(f"{BASE}/recommendations", headers=h1, json={"company_id": company(c, h1),
                     "purpose_category": "equipment", "amount": 8_000_000, "term_months": 60}).json()
        check("TC-15", "Подбор: оборудование → инвестиционный", rec[0]["product_code"] == "investment",
              ", ".join(f"{x['product_code']}:{x['match']}" for x in rec))

        # TC-16: данные компании не вводятся — анкета их не принимает
        schema = c.get(f"{BASE}/openapi.json").json()["components"]["schemas"]["ApplicationIn"]["properties"]
        company_fields = {"inn", "avg_monthly_revenue", "avg_monthly_expenses", "existing_monthly_payments", "okved"}
        check("TC-16", "Данные компании не вводятся вручную", not (company_fields & set(schema)),
              "поля анкеты: " + ", ".join(schema))

        # TC-18: клиент не видит PD
        a = c.get(f"{BASE}/applications/{foreign['id']}", headers=h1).json()
        check("TC-18", "Клиент не видит PD и группу риска",
              not {"pd", "score", "risk_band", "checks"} & set(a) and set(a["risk_factors"][0]) == {"text", "effect"})

        # TC-19: лимит попыток входа (последним — блокирует client2 на минуту)
        codes = [c.post(f"{BASE}/auth/login", json={"login": "client2", "password": "wrong"}).status_code for _ in range(6)]
        check("TC-19", "6-я неудачная попытка входа → 429", codes[:5] == [401] * 5 and codes[5] == 429, str(codes))

    width = max(len(r[0]) for r in results)
    for case_id, ok, text in results:
        print(f"{'PASS' if ok else 'FAIL'}  {case_id:<{width}}  {text}")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} сценариев прошли")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
