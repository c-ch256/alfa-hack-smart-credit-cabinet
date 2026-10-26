"""Представление демонстрационного договора и сохранённого графика в PDF."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_PATH = Path(__file__).resolve().parents[1] / 'assets' / 'NotoSans-Regular.ttf'
FONT_NAME = 'CraftorsNotoSans'
INK = colors.HexColor('#0b1f35')
MUTED = colors.HexColor('#6d7986')
LINE = colors.HexColor('#dde1e6')
BACKGROUND = colors.HexColor('#f3f4f5')


def _money(value: int) -> str:
    return f'{value:,}'.replace(',', ' ') + ' ₽'


def _date(value: str) -> str:
    return date.fromisoformat(value).strftime('%d.%m.%Y')


def _rate(value: str) -> str:
    number = format(Decimal(value), 'f')
    if '.' in number:
        number = number.rstrip('0').rstrip('.')
    return f'{number}% годовых'


def render_contract_pdf(contract: dict) -> bytes:
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=52,
        bottomMargin=46,
        title=f'Демонстрационный кредитный договор {contract["number"]}',
        author='Кредитный кабинет предпринимателя',
        invariant=1,
    )
    body = ParagraphStyle('body', fontName=FONT_NAME, fontSize=9, leading=13, textColor=INK)
    right = ParagraphStyle('right', parent=body, alignment=TA_RIGHT)
    title = ParagraphStyle('title', parent=body, fontSize=18, leading=24, spaceAfter=10)
    heading = ParagraphStyle(
        'heading', parent=body, fontSize=12, leading=17,
        spaceBefore=15, spaceAfter=9, keepWithNext=True,
    )
    subtitle = ParagraphStyle(
        'subtitle', parent=body, fontSize=10, leading=14,
        spaceBefore=10, spaceAfter=7, keepWithNext=True,
    )
    notice = ParagraphStyle(
        'notice', parent=body, fontSize=9, leading=14,
        textColor=colors.HexColor('#9b361b'), backColor=colors.HexColor('#fff3d0'),
        borderPadding=9, spaceBefore=13, spaceAfter=13,
    )

    def paragraph(value: object, style: ParagraphStyle = body) -> Paragraph:
        # Значения профиля являются текстом, а не разметкой ReportLab.
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), style)

    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BACKGROUND),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
        ('LEFTPADDING', (0, 0), (-1, -1), 9),
        ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ])

    def parameters(rows: list[tuple[str, str]], *, numeric: bool = False) -> Table:
        table = Table(
            [[paragraph('Параметр'), paragraph('Значение', right if numeric else body)]]
            + [[paragraph(label), paragraph(value, right if numeric else body)]
               for label, value in rows],
            colWidths=[155, document.width - 155],
            repeatRows=1,
            splitByRow=1,
            splitInRow=1,
            hAlign='LEFT',
        )
        table.setStyle(table_style)
        return table

    story = [
        paragraph('Демонстрационный кредитный договор', title),
        paragraph(f'№ {contract["number"]} от {_date(contract["contract_date"])}'),
        paragraph('ДЕМОНСТРАЦИОННЫЙ ДОКУМЕНТ. НЕ ИМЕЕТ ЮРИДИЧЕСКОЙ СИЛЫ.', notice),
        paragraph('Стороны договора', heading),
        paragraph('Кредитор', subtitle),
    ]
    bank = contract['bank']
    borrower = contract['borrower']
    story.append(parameters([
        ('Наименование', bank['name']),
        ('ИНН', bank['inn']),
        ('ОГРН', bank['ogrn']),
        ('Адрес', bank['legal_address']),
        ('Расчётный счёт', bank['settlement_account']),
        ('БИК', bank['bik']),
        ('Корреспондентский счёт', bank['correspondent_account']),
    ]))
    registration_label = 'ОГРН' if borrower['legal_form'] == 'OOO' else 'ОГРНИП'
    registration = borrower['ogrn'] if borrower['legal_form'] == 'OOO' else borrower['ogrnip']
    story.extend([
        paragraph('Заёмщик', subtitle),
        parameters([
            ('Наименование', borrower['name']),
            ('ИНН', borrower['inn']),
            (registration_label, registration or 'Не указан'),
            ('Адрес', borrower['legal_address']),
            ('Расчётный счёт', borrower['settlement_account']),
            ('Банк', borrower['bank_name']),
            ('БИК', borrower['bank_bik']),
            ('Корреспондентский счёт', borrower['correspondent_account'] or 'Не указан'),
        ]),
        paragraph('Условия кредита', heading),
        parameters([
            ('Продукт', contract['product_name']),
            ('Цель кредита', contract['purpose']),
        ]),
        Spacer(1, 8),
        parameters([
            ('Сумма кредита', _money(contract['amount_rub'])),
            ('Годовая ставка', _rate(contract['annual_rate_percent'])),
            ('Срок кредита', f'{contract["term_months"]} мес.'),
            ('Ежемесячный платёж', _money(contract['monthly_payment_rub'])),
            ('Всего платежей', _money(contract['total_payments_rub'])),
            ('Переплата', _money(contract['overpayment_rub'])),
        ], numeric=True),
        paragraph('Порядок погашения', heading),
        parameters([('Способ погашения', contract['repayment_method'])]),
        paragraph('Залог', heading),
    ])
    if contract['collateral_type']:
        collateral = [('Вид залога', contract['collateral_type'])]
        if contract['collateral_value_rub'] is not None:
            collateral.append(('Стоимость залога', _money(contract['collateral_value_rub'])))
        story.append(parameters(collateral, numeric=True))
    else:
        story.append(parameters([('Залог', 'Не предусмотрен')]))
    signing_labels = {
        'preview': 'Предварительный просмотр. Предложение ещё не принято; договор не подписан.',
        'accepted': 'Предложение принято. Договор ещё не подписан.',
        'signed_demo': 'Подписан в демонстрационном режиме.',
    }
    story.extend([
        paragraph('Демо-подписание', heading),
        parameters([('Состояние', signing_labels.get(
            contract['signing_status'], 'Состояние демо-подписания не указано'
        ))]),
        PageBreak(),
        paragraph('График платежей', heading),
        paragraph(f'К договору № {contract["number"]} от {_date(contract["contract_date"])}'),
        Spacer(1, 12),
    ])
    small = ParagraphStyle('schedule', parent=body, fontSize=8.5, leading=12, alignment=TA_RIGHT)
    small_left = ParagraphStyle('schedule-left', parent=small, alignment=TA_LEFT)
    headers = ['№', 'Дата платежа', 'Платёж, ₽', 'Проценты, ₽', 'Основной долг, ₽', 'Остаток, ₽']
    rows = [[paragraph(label, small_left if index == 1 else small)
             for index, label in enumerate(headers)]]
    for row in contract['schedule']:
        values = [str(row['number']), _date(row['payment_date']),
                  _money(row['payment_rub']), _money(row['interest_rub']),
                  _money(row['principal_rub']), _money(row['balance_rub'])]
        rows.append([paragraph(value, small_left if index == 1 else small)
                     for index, value in enumerate(values)])
    schedule = Table(
        rows,
        colWidths=[28, 77, 98, 96, 108, document.width - 407],
        repeatRows=1,
        splitByRow=1,
        hAlign='LEFT',
    )
    schedule.setStyle(table_style)
    schedule.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(schedule)

    def decorate_page(pdf, _document) -> None:
        pdf.saveState()
        pdf.setFont(FONT_NAME, 8)
        pdf.setFillColor(MUTED)
        pdf.drawString(40, A4[1] - 30, 'Демонстрационный документ · Не имеет юридической силы')
        pdf.drawRightString(A4[0] - 40, 25, f'Страница {pdf.getPageNumber()}')
        pdf.restoreState()

    document.build(story, onFirstPage=decorate_page, onLaterPages=decorate_page)
    return output.getvalue()
