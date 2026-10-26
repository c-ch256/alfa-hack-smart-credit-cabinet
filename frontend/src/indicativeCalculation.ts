export function indicativeRate(value: unknown): number | null {
  if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) return null
  const rate = Number(value)
  return Number.isFinite(rate) && rate >= 0 ? rate : null
}

export function indicativePayment(amount: number, months: number, annualRate: number | null): number | null {
  if (annualRate === null || !Number.isFinite(annualRate) || annualRate < 0
    || !Number.isSafeInteger(amount) || amount <= 0 || !Number.isSafeInteger(months) || months <= 0) return null
  const monthlyRate = annualRate / 1200
  const result = monthlyRate === 0 ? amount / months
    : amount * monthlyRate / -Math.expm1(-months * Math.log1p(monthlyRate))
  return Number.isFinite(result) ? Math.round(result) : null
}

export function indicativeRateLabel(rate: number | null) {
  return rate === null ? '—' : `от ${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 }).format(rate)}%`
}
