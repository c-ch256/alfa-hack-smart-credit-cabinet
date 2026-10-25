"""Воспроизводимый PDF с локальным кириллическим шрифтом и графиком."""

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_PATH = Path(__file__).resolve().parents[1] / 'assets' / 'NotoSans-Regular.ttf'
FONT_NAME = 'CraftorsNotoSans'


def render_contract_pdf(contract: dict) -> bytes:
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4, invariant=1, pageCompression=1)
    pdf.setTitle(f'Демонстрационный договор {contract["number"]}')
    width, height = A4
    y = height - 72

    def banner() -> None:
        pdf.setFillColor(colors.darkred)
        pdf.setFont(FONT_NAME, 10)
        pdf.drawString(40, height - 42, contract['notice'])
        pdf.setFillColor(colors.black)

    banner()

    def line(value: str, *, size: int = 10) -> None:
        nonlocal y
        max_width = width - 80
        words = value.split()
        chunks = []
        current = ''
        for word in words:
            candidate = f'{current} {word}'.strip()
            if current and pdfmetrics.stringWidth(candidate, FONT_NAME, size) > max_width:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
        for chunk in chunks:
            if y < 48:
                pdf.showPage()
                banner()
                y = height - 72
            pdf.setFont(FONT_NAME, size)
            pdf.drawString(40, y, chunk)
            y -= size + 7

    line(f'Договор № {contract["number"]} от {contract["contract_date"]}')
    bank = contract['bank']
    borrower = contract['borrower']
    line(f'Кредитор: {bank["name"]}; БИК {bank["bik"]}; корсчёт {bank["correspondent_account"]}')
    line(f'ИНН кредитора: {bank["inn"]}; ОГРН: {bank["ogrn"]}; счёт: {bank["settlement_account"]}')
    line(f'Адрес кредитора: {bank["legal_address"]}')
    number_label = 'ОГРН' if borrower['legal_form'] == 'OOO' else 'ОГРНИП'
    number = borrower['ogrn'] if borrower['legal_form'] == 'OOO' else borrower['ogrnip']
    line(f'Заёмщик: {borrower["name"]}; ИНН {borrower["inn"]}; {number_label} {number}')
    line(f'Адрес: {borrower["legal_address"]}')
    line(
        f'Счёт: {borrower["settlement_account"]}; банк: {borrower["bank_name"]}; '
        f'БИК {borrower["bank_bik"]}; корсчёт {borrower["correspondent_account"] or "не указан"}'
    )
    line(f'Продукт: {contract["product_name"]}; цель: {contract["purpose"]}')
    line(
        f'Сумма: {contract["amount_rub"]} ₽; ставка: {contract["annual_rate_percent"]}% годовых; '
        f'срок: {contract["term_months"]} мес.'
    )
    line(
        f'Ежемесячный платёж: {contract["monthly_payment_rub"]} ₽; '
        f'всего: {contract["total_payments_rub"]} ₽; '
        f'переплата: {contract["overpayment_rub"]} ₽'
    )
    line(f'Погашение: {contract["repayment_method"]}')
    line(
        f'Обеспечение: {contract["collateral_type"] or "не предусмотрено"}; '
        f'стоимость: {contract["collateral_value_rub"] or 0} ₽'
    )
    line(f'Статус демо-подписания: {contract["signing_status"]}')
    line('Приложение: график платежей (также доступен в JSON по ссылке договора).')
    line('№    Дата          Платёж ₽     Проценты ₽     Долг ₽       Остаток ₽')
    for row in contract['schedule']:
        line(
            f'{row["number"]:02d}   {row["payment_date"]}   {row["payment_rub"]}   '
            f'{row["interest_rub"]}   {row["principal_rub"]}   {row["balance_rub"]}',
            size=9,
        )
    pdf.save()
    return output.getvalue()
