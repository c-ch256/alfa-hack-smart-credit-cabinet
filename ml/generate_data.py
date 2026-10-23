# ml/generate_data.py
"""
Генератор синтетических данных для ML-скоринга МСБ.

Создаёт 15 тыс. исторических "выданных кредитов" с признаками из ScoringRequest
и меткой дефолта default_12m (просрочка 90+ дней в течение 12 месяцев после выдачи).

Метка генерируется через латентный риск (логит от признаков с нелинейностями).
Базовый уровень дефолта: 5-8%.

Использование:
    python generate_data.py
    python generate_data.py --n_samples 15000 --seed 42 --output data/synthetic_data.csv
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


# Константы
PRODUCTS = ['working_capital', 'investment']
LEGAL_FORMS = ['IP', 'OOO']  # ИП ~60%, ООО ~40%
OKVED_SECTIONS = ['A', 'C', 'D', 'F', 'G', 'H', 'I', 'K', 'L', 'M', 'N', 'Q']
REGIONS = ['Москва', 'Санкт-Петербург', 'Московская область', 'Свердловская область',
           'Краснодарский край', 'Новосибирская область', 'Татарстан', 'Другие']
# Фиксированное учебное допущение, не актуальная рыночная ставка.
AS_OF_DATE = '2026-10-23'
OKVED_CODES = dict(zip(OKVED_SECTIONS, ['01.11', '10.71', '35.11', '41.20',
    '47.11', '49.41', '56.10', '64.19', '68.20', '69.10', '81.21', '86.21']))
INDUSTRY_RISK = {'A': 0.15, 'C': -0.1, 'D': -0.2, 'F': 0.45,
                 'G': 0.1, 'H': 0.15, 'I': 0.35, 'K': -0.15,
                 'L': 0.05, 'M': -0.1, 'N': 0.1, 'Q': -0.2}


def generate_company_data(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Генерация данных о компании."""
    # ОПФ: ИП ~60%, ООО ~40%
    legal_form = rng.choice(LEGAL_FORMS, size=n, p=[0.6, 0.4])
    
    # ОКВЭД-секции с разным базовым риском
    okved_weights = [0.05, 0.15, 0.05, 0.10, 0.20, 0.05, 0.10, 0.05, 0.05, 0.10, 0.05, 0.05]
    okved_section = rng.choice(OKVED_SECTIONS, size=n, p=okved_weights)
    
    # Регион
    region = rng.choice(REGIONS, size=n)
    
    # Возраст бизнеса: 0-25 лет (0-300 месяцев), большинство 1-10 лет
    business_age_months = np.clip(
        rng.lognormal(mean=4.5, sigma=0.8, size=n).astype(int),
        0, 300
    )
    
    # Срок клиентских отношений с банком: 0-20 лет
    bank_client_months = np.clip(
        rng.lognormal(mean=3.5, sigma=1.0, size=n).astype(int),
        0, 240
    )
    # bank_client_months не может быть больше business_age_months
    bank_client_months = np.minimum(bank_client_months, business_age_months)
    
    # Выручка: логнормальное распределение, 100 тыс - 10 млн/мес
    avg_monthly_revenue = np.clip(
        rng.lognormal(mean=13.5, sigma=1.2, size=n),
        100000, 10000000
    ).round(-3)  # округление до тысяч
    
    # Расходы: 60-90% от выручки
    expense_ratio = rng.uniform(0.6, 1.05, size=n)
    avg_monthly_expenses = (avg_monthly_revenue * expense_ratio).round(-3)
    
    # Тренд выручки за 6 мес: -20% до +30%
    revenue_trend_6m = rng.uniform(-0.20, 0.30, size=n).round(3)
    
    # Существующие ежемесячные платежи: 0-500 тыс
    existing_monthly_payments = np.clip(
        rng.lognormal(mean=10.5, sigma=1.5, size=n),
        0, 500000
    ).round(-3)
    
    return pd.DataFrame({
        'legal_form': legal_form,
        'okved': [OKVED_CODES[section] for section in okved_section],
        'region': region,
        'business_age_months': business_age_months,
        'bank_client_months': bank_client_months,
        'avg_monthly_revenue': avg_monthly_revenue.astype(int),
        'avg_monthly_expenses': avg_monthly_expenses.astype(int),
        'revenue_trend_6m': revenue_trend_6m,
        'existing_monthly_payments': existing_monthly_payments.astype(int),
    })


def generate_bki_data(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Генерация данных БКИ."""
    # Активные кредиты: 0-5
    active_loans = np.clip(rng.poisson(lam=1.2, size=n), 0, 5)
    
    # Максимальная просрочка за 12 мес: 0-90 дней после стоп-факторов.
    # Большинство без просрочек, некоторые с небольшими, редкие с большими
    max_overdue_days_12m = np.zeros(n, dtype=int)
    has_overdue = rng.random(size=n) < 0.25  # 25% имеют просрочку
    max_overdue_days_12m[has_overdue] = np.clip(
        rng.lognormal(mean=2.5, sigma=1.0, size=has_overdue.sum()).astype(int),
        1, 90
    )
    
    # Общий долг: 0-10 млн
    total_debt = np.clip(
        rng.lognormal(mean=12.5, sigma=1.5, size=n),
        0, 10000000
    ).round(-3)
    
    # Дефолты в истории: 0-3
    # Выданные кредиты после стоп-факторов MVP: исторических дефолтов нет.
    defaults = np.zeros(n, dtype=int)
    total_debt[active_loans == 0] = 0
    
    return pd.DataFrame({
        'active_loans': active_loans,
        'max_overdue_days_12m': max_overdue_days_12m,
        'total_debt': total_debt.astype(int),
        'defaults': defaults,
    })


def generate_fns_data(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Генерация данных ФНС."""
    # Выданные кредиты после стоп-факторов: статус на дату заявки active.
    
    # Налоговый долг: 10% имеют
    tax_debt = rng.random(size=n) < 0.10
    
    # Массовый адрес: 5% имеют
    mass_address = rng.random(size=n) < 0.05
    
    return pd.DataFrame({
        'status': np.full(n, 'active'),
        'tax_debt': tax_debt,
        'mass_address': mass_address,
    })


def generate_loan_data(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Генерация параметров кредита."""
    # Продукт
    product_code = rng.choice(PRODUCTS, size=n, p=[0.75, 0.25])
    
    # Сумма кредита: зависит от продукта
    amount = np.zeros(n)
    for i, prod in enumerate(product_code):
        if prod == 'working_capital':
            amount[i] = rng.lognormal(mean=13.5, sigma=1.0)  # 300 тыс - 30 млн
        elif prod == 'investment':
            amount[i] = rng.lognormal(mean=15.0, sigma=1.2)  # 1-100 млн
    
    amount = np.where(product_code == 'working_capital',
                      np.clip(amount, 300000, 30000000),
                      np.clip(amount, 1000000, 100000000)).round(-3).astype(int)
    
    # Срок: зависит от продукта
    term_months = np.zeros(n, dtype=int)
    for i, prod in enumerate(product_code):
        if prod == 'working_capital':
            term_months[i] = rng.choice([3, 6, 12, 24, 36])
        elif prod == 'investment':
            term_months[i] = rng.choice([12, 24, 36, 60, 84])
    
    # Стоимость залога: для investment часто есть, для working_capital редко
    collateral_value = np.zeros(n)
    has_collateral = rng.random(size=n) < 0.3  # 30% имеют залог
    has_collateral |= (product_code == 'investment') & (amount >= 5000000)
    collateral_value[has_collateral] = np.clip(
        rng.lognormal(mean=14.0, sigma=1.5, size=has_collateral.sum()),
        100000, 50000000
    ).round(-3)
    
    return pd.DataFrame({
        'product_code': product_code,
        'amount': amount,
        'term_months': term_months,
        'collateral_value': collateral_value.astype(int),
    })


def generate_application_dates(n: int, rng: np.random.Generator) -> pd.Series:
    """24 месяца выдач с полными 12 месяцами исходов на AS_OF_DATE."""
    end_date = pd.Timestamp(AS_OF_DATE) - pd.DateOffset(months=12)
    start_date = end_date - pd.DateOffset(months=24) + pd.Timedelta(days=1)
    offsets = rng.integers(0, (end_date - start_date).days + 1, size=n)
    return pd.Series(start_date + pd.to_timedelta(offsets, unit='D'))


def compute_default_label(
    company: pd.DataFrame,
    bki: pd.DataFrame,
    fns: pd.DataFrame,
    loan: pd.DataFrame,
    rng: np.random.Generator,
    target_default_rate: float = 0.065
) -> pd.Series:
    """
    Вычисление метки дефолта через латентный риск (логит-модель).
    
    Направления влияния:
    - риск ↑: просрочки, дефолты в истории, налоговый долг, массовый адрес,
              высокая долговая нагрузка, низкое покрытие основного долга, падающая выручка,
              большой кредит к выручке
    - риск ↓: длинная история бизнеса и в банке, хорошее покрытие залогом,
              растущая выручка
    """
    if not 0 < target_default_rate < 1:
        raise ValueError('target_default_rate должен быть между 0 и 1')
    # Нормализация признаков для логит-модели
    revenue = company['avg_monthly_revenue']
    expenses = company['avg_monthly_expenses']
    business_age = company['business_age_months']
    bank_client = company['bank_client_months']
    revenue_trend = company['revenue_trend_6m']
    existing_payments = company['existing_monthly_payments']
    
    max_overdue = bki['max_overdue_days_12m']
    total_debt = bki['total_debt']
    defaults = bki['defaults']
    
    tax_debt = fns['tax_debt'].astype(int)
    mass_address = fns['mass_address'].astype(int)
    
    amount = loan['amount']
    collateral = loan['collateral_value']
    term_months = loan['term_months']
    
    # Производные признаки
    net_cash_flow = revenue - expenses
    # Только основной долг / срок; это не аннуитет и не финальный DSCR.
    principal_coverage = net_cash_flow / (existing_payments + amount / term_months + 1)
    loan_to_annual_revenue = amount / (revenue * 12 + 1)
    collateral_coverage = collateral / (amount + 1)
    
    # Логит-модель с нелинейностями и взаимодействиями
    logit = (
        # Базовый риск
        -2.5
        
        # Просрочки и дефолты (риск ↑)
        + 0.02 * max_overdue
        + 0.8 * defaults
        + 0.3 * (max_overdue > 30).astype(int)  # нелинейность: резкий скачок после 30 дней
        
        # Налоговый долг и массовый адрес (риск ↑)
        + 1.2 * tax_debt
        + 1.5 * mass_address
        
        # Долговая нагрузка (риск ↑)
        + 2.0 * loan_to_annual_revenue
        + 1.0 * np.log1p(total_debt / (revenue * 12))
        - 1.5 * np.clip(principal_coverage, -2, 5)
        + company['okved'].map({OKVED_CODES[k]: v for k, v in INDUSTRY_RISK.items()})
        
        # История бизнеса (риск ↓)
        - 0.005 * business_age
        - 0.003 * bank_client
        
        # Выручка и тренд
        - 0.0000001 * revenue  # большая выручка = немного меньший риск
        - 1.0 * revenue_trend  # падающая выручка = высокий риск
        
        # Залог (риск ↓)
        - 0.5 * collateral_coverage
        
        # Взаимодействие: высокая нагрузка × молодой бизнес = дополнительный риск
        + 0.0001 * loan_to_annual_revenue * (300 - business_age)
        
        # Случайный шум
        + rng.normal(0, 0.3, size=len(company))
    )
    
    # Настраиваем среднюю вероятность, а не точное число дефолтов.
    # Это настройка синтетического мира, не калибровка обученной модели.
    low, high = -100.0, 100.0
    for _ in range(80):
        intercept = (low + high) / 2
        default_prob = 1 / (1 + np.exp(-np.clip(logit + intercept, -60, 60)))
        if default_prob.mean() < target_default_rate:
            low = intercept
        else:
            high = intercept
    default_prob = 1 / (1 + np.exp(-np.clip(logit + (low + high) / 2, -60, 60)))
    default_12m = rng.binomial(1, default_prob)
    
    return default_12m


def generate_data(n_samples: int = 15000, seed: int = 42) -> pd.DataFrame:
    """Основная функция генерации данных."""
    if n_samples < 1:
        raise ValueError('n_samples должен быть положительным')
    rng = np.random.default_rng(seed)
    
    print(f"Генерация {n_samples} записей с seed={seed}...")
    
    # Генерация компонентов
    company = generate_company_data(n_samples, rng)
    bki = generate_bki_data(n_samples, rng)
    fns = generate_fns_data(n_samples, rng)
    loan = generate_loan_data(n_samples, rng)
    # Нет активных кредитов — нет текущих платежей по ним.
    company.loc[bki['active_loans'] == 0, 'existing_monthly_payments'] = 0
    # Долг согласован с платежами (условно 6–30 оставшихся платежей).
    bki['total_debt'] = (company['existing_monthly_payments'] * rng.uniform(6, 30, n_samples)).round(-3).astype(int)
    application_date = generate_application_dates(n_samples, rng)
    
    # Вычисление метки дефолта
    default_12m = compute_default_label(company, bki, fns, loan, rng)
    
    # Сборка DataFrame
    df = pd.concat([
        loan,
        company,
        bki,
        fns,
        pd.Series(application_date, name='application_date'),
        pd.Series(default_12m, name='default_12m')
    ], axis=1)
    
    # Статистика
    print(f"\nСтатистика:")
    print(f"  Всего записей: {len(df)}")
    print(f"  Дефолтов: {default_12m.sum()} ({default_12m.mean()*100:.2f}%)")
    print(f"  Продукты: {df['product_code'].value_counts().to_dict()}")
    print(f"  ОПФ: {df['legal_form'].value_counts().to_dict()}")
    print(f"  Период: {df['application_date'].min()} — {df['application_date'].max()}")
    
    return df


def main():
    print("=== НАЧАЛО ВЫПОЛНЕНИЯ ===")
    parser = argparse.ArgumentParser(description='Генератор синтетических данных для МСБ скоринга')
    parser.add_argument('--n_samples', type=int, default=15000, help='Количество записей (по умолчанию: 15000)')
    parser.add_argument('--seed', type=int, default=42, help='Seed для воспроизводимости (по умолчанию: 42)')
    parser.add_argument('--output', type=str, default='data/synthetic_data.csv', help='Путь для сохранения (по умолчанию: data/synthetic_data.csv)')
    
    args = parser.parse_args()
    
    # Создание директории, если нужно
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    
    # Генерация данных
    df = generate_data(n_samples=args.n_samples, seed=args.seed)
    
    # Сохранение
    df.to_csv(args.output, index=False, date_format='%Y-%m-%d', lineterminator='\n')
    print(f"\nДанные сохранены в {args.output}")
    
    # Сохранение метаданных
    metadata = {
        'n_samples': len(df),
        'seed': args.seed,
        'csv_sha256': hashlib.sha256(Path(args.output).read_bytes()).hexdigest(),
        'default_rate': float(df['default_12m'].mean()),
        'columns': list(df.columns),
        'as_of_date': AS_OF_DATE,
        'generator_version': '3.0.0-contract-v1',
        'target_default_rate': 0.065,
        'label': '90+ days overdue within 12 months after issuance',
        'limitations': [
            'Synthetic metrics do not establish quality on real data.',
            'Issued loans only; rejected applications excluded.',
            'MVP stop rules exclude historical defaults and overdue above 90 days.',
            'No temporal drift simulated.'
        ]
    }
    
    metadata_path = Path(args.output).with_suffix('.metadata.json')
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    print(f"Метаданные сохранены в {metadata_path}")


if __name__ == '__main__':
    main()
