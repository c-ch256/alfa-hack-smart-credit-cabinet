"""Продуктовые метрики по журналам status_events и audit_events (16_metrics.md).

Запуск (нужны sqlalchemy и драйвер БД: pip install sqlalchemy "psycopg[binary]"):
  python docs/product/metrics.py                                   # DATABASE_URL из окружения
  python docs/product/metrics.py postgresql+psycopg://user:pass@host/db
Работает с Postgres и SQLite: читает таблицы через рефлексию и считает в Python.
"""
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import MetaData, create_engine, select

DECISION = {"approved", "counter_offer", "rejected", "manual_review", "needs_info"}
FINAL = {"approved", "counter_offer", "rejected"}
OFFER = {"approved", "counter_offer"}

# Целевые значения MVP (01_product_ux_ba.md)
TARGET_TTD_SEC = 60
TARGET_AUTO_SHARE = 0.70
TARGET_FIELDS = 6
TARGET_INFO_SHARE = 0.15
TARGET_STAFF_SEC = 300

# Обязательные поля, которые вводит клиент (03_fields.md) — метрика не из журналов, а из справочника.
CLIENT_FIELDS = {"Оборотный": 3, "Инвестиционный до 5 млн ₽": 4, "Инвестиционный от 5 млн ₽": 6}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _load(url: str) -> tuple[dict[int, list], dict[int, list]]:
    engine = create_engine(url)
    meta = MetaData()
    meta.reflect(engine, only=["status_events", "audit_events"])
    se, ae = meta.tables["status_events"], meta.tables["audit_events"]
    events: dict[int, list] = defaultdict(list)
    audit: dict[int, list] = defaultdict(list)
    with engine.connect() as conn:
        for r in conn.execute(select(se).order_by(se.c.id)):
            events[r.application_id].append((r.from_status, r.to_status, r.actor_role, _utc(r.created_at)))
        for r in conn.execute(select(ae).where(ae.c.application_id.is_not(None)).order_by(ae.c.id)):
            audit[r.application_id].append((r.action, r.actor_role, _utc(r.created_at)))
    engine.dispose()
    return events, audit


def _share(num: int, den: int) -> float | None:
    return num / den if den else None


def compute(events: dict[int, list], audit: dict[int, list]) -> dict:
    ttd, staff_time = [], []
    submitted = finals = auto = offered = accepted = countered = counter_accepted = 0
    info_any = info_system = info_staff = 0

    for app_id, ev in events.items():
        sub = next((t for _, to, _, t in ev if to == "submitted"), None)
        if sub is None:
            continue  # черновики не считаем
        submitted += 1
        # 1. Time-to-decision: подача -> первый статус решения (включая запрос сведений и ручное рассмотрение)
        first = next(((t, actor) for _, to, actor, t in ev if to in DECISION and t >= sub), None)
        if first and first[1] == "SYSTEM":
            ttd.append((first[0] - sub).total_seconds())
        # 2. Доля автоматических решений среди дошедших до финального решения
        final = next(((to, actor) for _, to, actor, _ in ev if to in FINAL), None)
        if final:
            finals += 1
            if final[1] == "SYSTEM" and not any(to == "manual_review" for _, to, _, _ in ev):
                auto += 1
        # 4. Конверсии
        if any(to in OFFER for _, to, _, _ in ev):
            offered += 1
            accepted += any(to == "accepted" for _, to, _, _ in ev)
        if any(to == "counter_offer" for _, to, _, _ in ev):
            countered += 1
            counter_accepted += any(fr == "counter_offer" and to == "accepted" for fr, to, _, _ in ev)
        # 5. Запрос сведений
        info = [actor for _, to, actor, _ in ev if to == "needs_info"]
        info_any += bool(info)
        info_system += "SYSTEM" in info
        info_staff += "STAFF" in info
        # 6. Время сотрудника: первый просмотр карточки после попадания в очередь -> решение или запрос
        review_at = next((t for _, to, _, t in ev if to == "manual_review"), None)
        if review_at:
            acts = audit.get(app_id, [])
            view = next((t for a, _, t in acts if a == "staff.view" and t >= review_at), None)
            done = next((t for a, _, t in acts if a in ("staff.decision", "staff.request_info")
                         and view and t >= view), None)
            if view and done:
                staff_time.append((done - view).total_seconds())

    def p95(xs: list[float]) -> float | None:
        return sorted(xs)[max(int(len(xs) * 0.95 + 0.5) - 1, 0)] if xs else None

    return {
        "submitted": submitted,
        "ttd_median": statistics.median(ttd) if ttd else None, "ttd_p95": p95(ttd), "ttd_n": len(ttd),
        "auto_share": _share(auto, finals), "auto": auto, "finals": finals,
        "offer_accept": _share(accepted, offered), "accepted": accepted, "offered": offered,
        "counter_accept": _share(counter_accepted, countered), "counter_accepted": counter_accepted,
        "countered": countered,
        "info_share": _share(info_any, submitted), "info_any": info_any, "info_system": info_system,
        "info_staff": info_staff,
        "staff_median": statistics.median(staff_time) if staff_time else None, "staff_n": len(staff_time),
    }


def _fmt_share(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def _fmt_sec(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x * 1000:.0f} мс" if x < 1 else f"{x:.1f} с"


def report(m: dict) -> list[tuple[str, str, str, str]]:
    def mark(ok: bool | None) -> str:
        return "—" if ok is None else ("✓" if ok else "✗")

    fields_max = max(CLIENT_FIELDS.values())
    return [
        ("Time-to-decision (авто), медиана / p95", f"{_fmt_sec(m['ttd_median'])} / {_fmt_sec(m['ttd_p95'])} (n={m['ttd_n']})",
         "< 60 с", mark(None if m["ttd_p95"] is None else m["ttd_p95"] < TARGET_TTD_SEC)),
        ("Доля автоматических решений", f"{_fmt_share(m['auto_share'])} ({m['auto']}/{m['finals']})",
         "≥ 70%", mark(None if m["auto_share"] is None else m["auto_share"] >= TARGET_AUTO_SHARE)),
        ("Полей вводит клиент (обязательных)", ", ".join(f"{k}: {v}" for k, v in CLIENT_FIELDS.items()),
         "≤ 6", mark(fields_max <= TARGET_FIELDS)),
        ("Конверсия «оффер → принят»", f"{_fmt_share(m['offer_accept'])} ({m['accepted']}/{m['offered']})", "растёт", "—"),
        ("Конверсия «встречное → принято»", f"{_fmt_share(m['counter_accept'])} ({m['counter_accepted']}/{m['countered']})",
         "растёт", "—"),
        ("Доля заявок с запросом сведений", f"{_fmt_share(m['info_share'])} ({m['info_any']}/{m['submitted']}; "
         f"система {m['info_system']}, сотрудник {m['info_staff']})",
         "≤ 15%", mark(None if m["info_share"] is None else m["info_share"] <= TARGET_INFO_SHARE)),
        ("Время сотрудника на ручную заявку, медиана", f"{_fmt_sec(m['staff_median'])} (n={m['staff_n']})",
         "< 5 мин", mark(None if m["staff_median"] is None else m["staff_median"] < TARGET_STAFF_SEC)),
    ]


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATABASE_URL", "sqlite:///./local.db")
    rows = report(compute(*_load(url)))
    print("| Метрика | Значение | Цель | ✓ |\n|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
