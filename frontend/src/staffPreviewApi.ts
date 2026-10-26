import { numeric } from './staffApplicationDetail'
import type { Numeric } from './staffApplicationDetail'
import { validStaffProduct } from './staffProducts'
import type { StaffProduct } from './staffProducts'

export type LoanPreviewInput = {
  application_id: number
  product_code: string
  amount: number
  term_months: number
}
export type LoanPreview = Omit<LoanPreviewInput, 'product_code'> & {
  product: StaffProduct
  indicative: boolean
  demonstration: boolean
  snapshot_id: number
  snapshot_version: number
  pd: Numeric
  risk_band: string
  model_version: string
  annual_rate: Numeric
  monthly_payment: number
  dscr: Numeric
}

export async function loadLoanPreview(input: LoanPreviewInput, token: string, signal: AbortSignal,
  snapshotVersion: number, snapshotId?: number): Promise<LoanPreview> {
  let response: Response
  try {
    response = await fetch('/api/staff/loan-preview', {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(input), signal,
    })
  } catch (cause) {
    if (signal.aborted) throw cause
    throw new Error('Нет соединения с сервером. Повторите расчёт.', { cause })
  }
  if (!response.ok) {
    let code: unknown
    try { code = (await response.json()).code } catch { /* Do not expose server diagnostics. */ }
    const messages: Record<string, string> = {
      INVALID_PRODUCT: 'Продукт недоступен или не подходит для цели заявки.',
      INVALID_AMOUNT: 'Сумма выходит за ограничения выбранного продукта.',
      INVALID_TERM: 'Срок выходит за ограничения выбранного продукта.',
      MISSING_PRODUCT_DATA: 'Для выбранного продукта не хватает сведений в анкете.',
      SCORING_UNAVAILABLE: 'Расчёт временно недоступен. Попробуйте ещё раз.',
    }
    if (response.status === 401) throw new Error('Сессия завершилась. Войдите заново.')
    if ([403, 404].includes(response.status)) throw new Error('Заявка недоступна для расчёта.')
    throw new Error(typeof code === 'string' && Object.hasOwn(messages, code)
      ? messages[code] : 'Не удалось рассчитать условия. Проверьте поля и повторите расчёт.')
  }
  let data: LoanPreview
  try { data = await response.json() as LoanPreview } catch { throw new Error('Не удалось прочитать результат расчёта. Повторите запрос.') }
  if (!data || data.application_id !== input.application_id || !validStaffProduct(data.product) || data.product.code !== input.product_code
    || data.amount !== input.amount || data.term_months !== input.term_months
    || data.indicative !== true || data.demonstration !== true
    || !Number.isSafeInteger(data.snapshot_id) || data.snapshot_id <= 0
    || !Number.isSafeInteger(data.snapshot_version) || !['A', 'B', 'C', 'D'].includes(data.risk_band)
    || numeric(data.annual_rate) === null || Number(data.annual_rate) < 0 || numeric(data.dscr) === null
    || numeric(data.pd) === null || Number(data.pd) < 0 || Number(data.pd) > 1
    || !Number.isSafeInteger(data.monthly_payment) || data.monthly_payment < 0) {
    throw new Error('Сервер вернул несогласованный результат расчёта. Повторите запрос.')
  }
  if (data.snapshot_version !== snapshotVersion || snapshotId !== undefined && data.snapshot_id !== snapshotId) {
    throw new Error('Сведения заявки изменились. Откройте карточку заново перед расчётом.')
  }
  return data
}
