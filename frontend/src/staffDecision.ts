import type { StaffDetail, StaffCheck, StaffAction } from './staffApplicationDetail'
import { numeric, rubles } from './staffApplicationDetail'
import type { Session } from './auth'
import type { StaffProduct } from './staffProducts'

export function canMakeStaffDecision(detail: StaffDetail, session: Session, action: StaffAction) {
  const app = detail.application
  return session.user.role === 'STAFF' && app.status === 'manual_review'
    && app.assigned_staff_user_id === session.user.id
    && Array.isArray(detail.allowed_actions) && detail.allowed_actions.includes(action)
}

export function groupedAmount(value: string) {
  return value.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
}

export function counterFieldErrors(product: StaffProduct | undefined, amount: string, term: string) {
  const limits = product
  if (!limits) return { amount: 'Лимиты выбранного продукта недоступны.', term: 'Лимиты выбранного продукта недоступны.' }
  const validAmount = /^\d+$/.test(amount) && Number.isSafeInteger(Number(amount))
    && Number(amount) >= limits.min_amount && Number(amount) <= limits.max_amount
  const validTerm = /^\d+$/.test(term) && Number.isSafeInteger(Number(term))
    && Number(term) >= limits.min_term_months && Number(term) <= limits.max_term_months
  return {
    amount: validAmount ? '' : `Укажите сумму от ${rubles(limits.min_amount)} до ${rubles(limits.max_amount)}.`,
    term: validTerm ? '' : `Укажите целый срок от ${limits.min_term_months} до ${limits.max_term_months} мес.`,
  }
}

export type CheckPresentation = { tone: 'good' | 'warning' | 'neutral'; summary: string }
export function presentCheck(check: StaffCheck | undefined): CheckPresentation {
  const p = check?.payload
  if (!check || !p || p.available === false) return { tone: 'neutral', summary: 'Результат проверки недоступен' }
  const warn = check.status === 'warn' || check.status === 'fail'
  if (check.source === 'FNS') {
    const remarks = [p.status === 'liquidating' ? 'Компания в процессе ликвидации' : p.status === 'inactive' ? 'Компания не действует' : '',
      p.tax_debt === true ? 'Есть налоговая задолженность' : '', p.mass_address === true ? 'Массовый адрес' : ''].filter(Boolean)
    if (remarks.length) return { tone: 'warning', summary: remarks.join(' · ') }
    const complete = p.status === 'active' && typeof p.tax_debt === 'boolean' && typeof p.mass_address === 'boolean'
    return warn ? { tone: 'warning', summary: 'Есть замечания к сведениям компании' }
      : complete && check.status === 'ok' ? { tone: 'good', summary: 'Компания действует · налоговой задолженности нет' }
        : { tone: 'neutral', summary: 'Сведения компании неполны' }
  }
  if (check.source !== 'BKI') return { tone: 'neutral', summary: 'Результат проверки недоступен' }
  const loans = numeric(p.active_loans), debt = numeric(p.total_debt)
  const overdue = numeric(p.max_overdue_days_12m), defaults = numeric(p.defaults)
  const remarks = [overdue !== null && overdue > 0 ? `Просрочка за 12 месяцев: до ${overdue} дн.` : '',
    defaults !== null && defaults > 0 ? `Дефолты: ${defaults}` : ''].filter(Boolean)
  if (remarks.length) return { tone: 'warning', summary: remarks.join(' · ') }
  if (warn) return { tone: 'warning', summary: 'Есть замечания к кредитной истории' }
  const complete = [loans, debt, overdue, defaults].every((n) => n !== null && Number.isInteger(n) && n >= 0)
  return complete && check.status === 'ok'
    ? { tone: 'good', summary: `Действующие кредиты: ${loans} · долг ${rubles(debt)} · просрочек за 12 месяцев нет` }
    : { tone: 'neutral', summary: 'Сведения кредитной истории неполны' }
}

export type ManualDecision = 'approve' | 'counter' | 'reject'
export type DecisionPayload = { decision: ManualDecision; comment: string; product_code?: string; amount?: number; term_months?: number }
export const COMMENT_LIMIT = 2000
export const requestedFields = [
  { key: 'purpose_details', label: 'Дополнительные сведения о цели кредита' },
  { key: 'investment_object', label: 'Объект инвестирования' },
  { key: 'collateral_type', label: 'Вид залога' },
  { key: 'collateral_value', label: 'Оценочная стоимость залога' },
] as const
export type RequestInfoField = typeof requestedFields[number]['key']
export function publicInfoRequest(selected: RequestInfoField[]): string {
  const labels: Record<RequestInfoField, string> = { purpose_details: 'подробности цели кредита', investment_object: 'объект инвестиций', collateral_type: 'вид залога', collateral_value: 'стоимость залога' }
  return selected.length ? `Пожалуйста, дополните: ${selected.map((key) => labels[key]).join(', ')}.` : ''
}

export class StaffDecisionError extends Error {
  refreshRequired: boolean
  constructor(message: string, refreshRequired = false, cause?: unknown) {
    super(message, { cause })
    this.refreshRequired = refreshRequired
  }
}

export async function sendStaffDecision(id: number, token: string, payload: DecisionPayload | { fields: RequestInfoField[]; comment: string }): Promise<StaffDetail['application']> {
  const requestInfo = 'fields' in payload
  let response: Response
  try {
    response = await fetch(`/api/staff/applications/${id}/${requestInfo ? 'request-info' : 'decision'}`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch (cause) {
    throw new StaffDecisionError(cause instanceof Error && cause.name === 'AbortError'
      ? 'Запрос прерван. Обновите заявку, чтобы проверить результат.'
      : 'Не удалось подтвердить результат. Обновите заявку перед повторным действием.', true, cause)
  }
  if (!response.ok) {
    let code: unknown
    try { code = (await response.json()).code } catch { /* Never display raw responses. */ }
    const messages: Record<string, string> = {
      INVALID_INFO_FIELDS: 'Выбранные сведения не относятся к продукту заявки.',
      INVALID_PRODUCT: 'Продукт недоступен или не подходит для цели заявки.',
      INVALID_AMOUNT: 'Сумма выходит за ограничения выбранного продукта.',
      INVALID_TERM: 'Срок выходит за ограничения выбранного продукта.',
      MISSING_PRODUCT_DATA: 'Для выбранного продукта не хватает сведений в анкете.',
      UNCHANGED_OFFER: 'Измените продукт, сумму или срок предложения.',
      VALIDATION_ERROR: 'Проверьте введённые условия и внутренний комментарий.',
      SCORING_UNAVAILABLE: 'Оценка временно недоступна. Попробуйте позже.',
    }
    if (response.status === 401) throw new StaffDecisionError('Сессия завершилась. Войдите заново.', true)
    if ([403, 404].includes(response.status)) throw new StaffDecisionError('Заявка недоступна. Обновите данные.', true)
    if (response.status === 409) throw new StaffDecisionError('Заявка уже изменилась. Обновите данные перед решением.', true)
    if (response.status >= 500 && code !== 'SCORING_UNAVAILABLE') {
      throw new StaffDecisionError('Не удалось подтвердить результат. Обновите заявку перед повторным действием.', true)
    }
    throw new StaffDecisionError(typeof code === 'string' && Object.hasOwn(messages, code)
      ? messages[code] : 'Не удалось отправить решение. Проверьте условия и повторите запрос.')
  }
  let app: StaffDetail['application']
  try { app = await response.json() as StaffDetail['application'] } catch {
    throw new StaffDecisionError('Ответ не удалось прочитать. Обновите заявку, чтобы проверить результат.', true)
  }
  const expected = 'fields' in payload ? 'needs_info' : { approve: 'approved', counter: 'counter_offer', reject: 'rejected' }[payload.decision]
  if (app?.id !== id || app.status !== expected) {
    throw new StaffDecisionError('Обновите заявку, чтобы проверить результат решения.', true)
  }
  return app
}
