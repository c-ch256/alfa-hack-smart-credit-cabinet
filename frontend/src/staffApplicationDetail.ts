import type { SavedInfoResponse } from './staffAdditionalInfo'
import type { Session } from './auth'
import { staffStatusNames } from './staffApplications'
import type { StaffApplication } from './staffApplications'

export type Numeric = number | string | null | undefined
export type StaffOffer = {
  id: number
  kind: string
  product_code: string
  amount: number
  term_months: number
  rate: Numeric
  monthly_payment: number
  status: string
  created_at: string
}
export type StaffEvent = {
  company_name?: string | null
  sequence: number
  from_status: string | null
  to_status: string
  actor_role: string
  actor_user_id: number | null
  actor_display_name?: string | null
  reason: string | null
  created_at: string
}
export type StaffCheck = {
  source: string
  status: string
  summary: string
  payload: Record<string, unknown>
  adapter_version: string
  created_at: string
}
export type StaffAction = 'approve' | 'counter' | 'reject' | 'request_info'
type StaffDecision = {
  id: number
  decision: 'approve' | 'counter' | 'reject'
  created_by: 'STAFF' | 'SYSTEM'
  created_by_user_id: number | null
  internal_comment?: string | null
}
export type StaffDetail = {
  decisions?: StaffDecision[]
  allowed_actions?: StaffAction[]
  application: StaffApplication & {
    assigned_staff_user_id?: number | null
    term_months: number
    purpose: string
    purpose_details: string | null
    investment_object: string | null
    collateral_type: string | null
    collateral_value: number | null
    info_request: string | null
    info_response: string | null
    version: number
    created_at: string
  }
  company: {
    id: number
    name: string
    inn: string
    legal_form: string
    registration_number: string | null
    legal_address: string | null
    settlement_account: string | null
    bank_bik: string | null
    bank_name: string | null
    correspondent_account: string | null
    okved: string
    industry: string | null
    region: string
    registered_at: string
    bank_client_since: string
  }
  snapshot_version: number
  snapshot_id?: number
  additional_info_responses?: SavedInfoResponse[]
  initial_request?: {
    snapshot_id: number
    snapshot_version: number
    submitted_at: string
    product_code: string
    amount: number
    term_months: number
    purpose: string
    purpose_details: string | null
    investment_object: string | null
    collateral_type: string | null
    collateral_value: number | null
  } | null
  company_snapshot: Record<string, unknown>
  dscr: { snapshot_id: number; score_id: number | null; calculation_basis: 'current_formula' | 'saved_offer'; scope: 'original_application' | 'offer'; offer_id: number | null; snapshot_version: number; product_code: string | null; amount: number | null; term_months: number | null; rate: Numeric; value: Numeric; explanation: string | null }[]
  checks: StaffCheck[]
  score: {
    id: number
    snapshot_id?: number
    source: string
    result_status: string
    is_test_result: boolean
    pd: Numeric
    risk_band: string | null
    model_version: string
    reasons: { feature: string; text: string; effect: string; contribution: Numeric }[]
    error_code: string | null
    product_code: string | null
    amount: number | null
    term_months: number | null
    created_at: string
  } | null
  offers: StaffOffer[]
  history: StaffEvent[]
}

export class StaffDetailError extends Error {
  status: number
  constructor(status: number) {
    super(status === 401 ? 'Сессия завершилась. Войдите в кабинет заново.'
      : [403, 404].includes(status) ? 'Заявка не найдена или недоступна для просмотра.'
        : 'Не удалось загрузить заявку. Попробуйте ещё раз.')
    this.status = status
  }
}

export async function loadStaffDetail(id: number, token: string, signal: AbortSignal): Promise<StaffDetail> {
  let response: Response
  try {
    response = await fetch(`/api/staff/applications/${id}`, {
      headers: { Authorization: `Bearer ${token}` }, signal,
    })
  } catch (cause) {
    if (signal.aborted) throw cause
    throw new Error('Нет соединения с сервером. Попробуйте ещё раз.', { cause })
  }
  if (!response.ok) throw new StaffDetailError(response.status)
  let data: StaffDetail
  try { data = await response.json() as StaffDetail } catch {
    throw new Error('Не удалось прочитать данные заявки. Попробуйте ещё раз.')
  }
  if (data?.application?.id !== id || data.company?.id !== data.application.company_id ||
      !Array.isArray(data.dscr) || !Array.isArray(data.checks) || !Array.isArray(data.offers) || !Array.isArray(data.history) ||
      !data.company_snapshot || typeof data.company_snapshot !== 'object') {
    throw new Error('Не удалось прочитать данные заявки. Попробуйте ещё раз.')
  }
  return data
}

export function numeric(value: unknown): number | null {
  if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}
export function rubles(value: unknown): string {
  const number = numeric(value)
  return number === null ? 'Нет данных' : new Intl.NumberFormat('ru-RU', {
    style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2,
  }).format(number)
}
export function decimal(value: unknown, digits = 2): string {
  const number = numeric(value)
  return number === null ? 'Нет данных' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(number)
}
export function percent(value: unknown, fraction = false): string {
  const number = numeric(value)
  return number === null ? 'Нет данных' : `${decimal(number * (fraction ? 100 : 1), 4)}%`
}
export function dateLabel(value: string | null | undefined, withTime = true): string {
  if (!value) return 'Нет данных'
  // Calendar dates are parsed locally, without shifting YYYY-MM-DD through UTC.
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value)
  if (!Number.isFinite(date.getTime())) return 'Нет данных'
  const day = date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
  return withTime ? `${day}, ${date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` : day
}
export function statusLabel(status: string): string {
  return Object.hasOwn(staffStatusNames, status) ? staffStatusNames[status as keyof typeof staffStatusNames] : 'Статус не определён'
}
export function clientActorCompany(savedName?: string | null, companyName?: string | null): string | null {
  return savedName?.trim() || companyName?.trim() || null
}
export function actorLabel(event: StaffEvent, currentUser?: Session['user'], companyName?: string | null): string {
  if (event.actor_role === 'SYSTEM') return 'Система'
  if (event.actor_role === 'CLIENT') return clientActorCompany(event.company_name, companyName) || 'Клиент'
  const name = event.actor_display_name?.trim() || (
    currentUser?.id === event.actor_user_id && currentUser.role === event.actor_role
      ? currentUser.full_name.trim() : ''
  )
  if (event.actor_role === 'STAFF') return name || 'Сотрудник банка'
  return 'Инициатор не указан'
}
export function newestHistory(history: StaffEvent[]): StaffEvent[] {
  return [...history].sort((a, b) =>
    ((Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)) || b.sequence - a.sequence)
}
export function decisionEvent(detail: StaffDetail): StaffEvent | undefined {
  const status = detail.application.status
  return newestHistory(detail.history).find((event) =>
    ['approved', 'rejected', 'counter_offer'].includes(event.to_status) &&
    (!['approved', 'rejected', 'counter_offer'].includes(status) || event.to_status === status))
}
export function staffDecisionComment(detail: StaffDetail): string | null {
  const event = decisionEvent(detail)
  if (event?.actor_role !== 'STAFF') return null
  // The latest bank decision is authoritative, including SYSTEM decisions.
  // Never search backwards for any past STAFF comment or use general application notes.
  const decision = [...(detail.decisions ?? [])].sort((a, b) => b.id - a.id)[0]
  const status = { approve: 'approved', reject: 'rejected', counter: 'counter_offer' }
  if (!decision || !Number.isSafeInteger(decision.id) || decision.id <= 0
    || decision.created_by !== 'STAFF' || decision.created_by_user_id === null
    || decision.created_by_user_id !== event.actor_user_id
    || status[decision.decision] !== event.to_status) return null
  return decision.internal_comment?.trim() || null
}
export function currentBankOffer(detail: StaffDetail): StaffOffer | undefined {
  const status = detail.application.status
  const offers = [...detail.offers].sort((a, b) => b.id - a.id)
  if (['accepted', 'signed_demo', 'disbursed_demo'].includes(status)) {
    return offers.find((offer) => offer.status === 'accepted')
  }
  if (['approved', 'counter_offer'].includes(status)) {
    // Match backend's current proposal rule: highest offer ID, still proposed.
    return offers[0]?.status === 'proposed' ? offers[0] : undefined
  }
  if (status === 'closed') return offers.find((offer) => ['accepted', 'declined'].includes(offer.status))
  return undefined
}

export function scoreDscr(detail: StaffDetail) {
  const score = detail.score
  const conditions = score ?? detail.application
  const snapshotId = detail.snapshot_id ?? score?.snapshot_id
  const candidates = detail.dscr.filter((item) => item.scope === 'original_application' && item.offer_id === null
    && item.calculation_basis === 'current_formula' && Number.isSafeInteger(item.snapshot_id) && item.snapshot_id > 0
    && (snapshotId === undefined || item.snapshot_id === snapshotId)
    && item.score_id === (score?.id ?? null) && item.snapshot_version === detail.snapshot_version
    && item.product_code === conditions.product_code && item.amount === conditions.amount && item.term_months === conditions.term_months)
  // snapshot_version is unique within an application; score_id binds it to the displayed assessment.
  return candidates.length === 1 ? candidates[0] : undefined
}
