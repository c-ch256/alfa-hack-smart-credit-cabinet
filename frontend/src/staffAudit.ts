import { savedInfoResponse } from './staffAdditionalInfo'
import { staffGet, staffStatusNames } from './staffApplications'
import type { StaffApplication } from './staffApplications'
import { clientActorCompany, numeric } from './staffApplicationDetail'
import type { Session } from './auth'

export type StaffAuditEvent = {
  id: number
  additional_info?: unknown
  info_request_text?: string | null
  assigned_staff_user_id?: number | null
  assigned_staff_display_name?: string | null
  actor_display_name?: string | null
  company_name?: string | null
  status_at_event?: string | null
  decision_source?: 'SYSTEM' | 'STAFF' | null
  decision_id?: number | null
  status_event_id?: number | null
  comment_id?: number | null
  internal_comment?: string | null
  offer_id?: number | null
  offer_terms?: Record<string, unknown> | null
  actor_user_id: number | null
  actor_role: string
  application_id: number | null
  action: string
  result: string
  rule_version: string | null
  details: Record<string, unknown>
  created_at: string
}
export type AuditRow = StaffAuditEvent & { applicationNumber: string | null; companyName: string | null }
export type AuditFilters = { q: string; from: string; to: string; actor: string; action: string; sort: 'newest' | 'oldest' }
export const auditDefaults: AuditFilters = { q: '', from: '', to: '', actor: 'all', action: 'all', sort: 'newest' }

// Supported combinations follow the server FSM and action endpoints, not event counts.
const actionDictionary: Record<string, { label: string; roles: readonly string[] }> = {
  'application.submitted': { label: 'Заявка отправлена', roles: ['CLIENT'] },
  'application.manual_review': { label: 'Заявка передана специалисту', roles: ['SYSTEM'] },
  'application.approved': { label: 'Заявка одобрена', roles: ['SYSTEM', 'STAFF'] },
  'application.rejected': { label: 'В кредите отказано', roles: ['SYSTEM', 'STAFF'] },
  'application.counter_offer': { label: 'Предложены другие условия', roles: ['SYSTEM', 'STAFF'] },
  'application.needs_info': { label: 'Запрошены дополнительные сведения', roles: ['SYSTEM', 'STAFF'] },
  'application.checking': { label: 'Получены дополнительные сведения', roles: ['CLIENT'] },
  'offer.accept': { label: 'Предложение принято', roles: ['CLIENT'] },
  'offer.decline': { label: 'Предложение отклонено', roles: ['CLIENT'] },
  'application.withdrawn': { label: 'Заявка отозвана', roles: ['CLIENT'] },
  'application.closed': { label: 'Заявка закрыта', roles: ['CLIENT'] },
  'application.signed_demo': { label: 'Договор подписан (демо)', roles: ['CLIENT'] },
  'application.disbursed_demo': { label: 'Кредит выдан (демо)', roles: ['SYSTEM'] },
  'sign_demo.invalid_code': { label: 'Неверный код подписания', roles: ['CLIENT'] },
}
// Keep other significant events when present, without inventing event records.
const extraActionLabels: Record<string, string> = {
  'staff.comment.create': 'Добавлен внутренний комментарий',
  'workflow.technical_error': 'Ошибка обработки заявки',
}
const decisionActions: Record<string, string> = {
  approve: 'application.approved', reject: 'application.rejected', counter: 'application.counter_offer',
  needs_info: 'application.needs_info', manual_review: 'application.manual_review',
}
function actionLabel(key: string) {
  if (Object.hasOwn(actionDictionary, key)) return actionDictionary[key].label
  return Object.hasOwn(extraActionLabels, key) ? extraActionLabels[key] : 'Другое действие'
}
const hiddenActions = new Set([
  'staff.view', 'staff.list_view', 'staff.audit_view', 'staff.loan_preview', 'application.scoring', 'application.decision', 'workflow.policy',
  'application.create', 'application.draft', 'application.edit', 'sign_demo.code_issued', 'staff.comments_view',
])
export function isVisibleAuditEvent(event: StaffAuditEvent) {
  // In the current FSM only needs_info → checking is initiated by CLIENT.
  // The SYSTEM transition to checking merely starts internal checks.
  if (event.action === 'application.checking') return event.actor_role === 'CLIENT'
  return !hiddenActions.has(event.action)
}
export function auditDecisionLabel(event: StaffAuditEvent) {
  const decision = event.details.decision
  return typeof decision === 'string' && Object.hasOwn(decisionActions, decision)
    ? actionLabel(decisionActions[decision]) : null
}
function savedId(value: unknown): value is number { return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 }
export function auditInternalComment(event: StaffAuditEvent): string | null {
  // Enriched by the backend from this event's decision_id/comment_id, never from a common note.
  if (event.actor_role !== 'STAFF' || !savedId(event.decision_id) && !savedId(event.comment_id)) return null
  return typeof event.internal_comment === 'string' ? event.internal_comment.trim() || null : null
}
export function auditInfoResponse(event: StaffAuditEvent) {
  return event.actor_role === 'CLIENT' && event.action === 'application.checking'
    ? savedInfoResponse(event.additional_info, event.id) : null
}
export function auditInfoRequestText(event: StaffAuditEvent): string | null {
  return ['staff.request_info', 'application.needs_info'].includes(event.action)
    && typeof event.info_request_text === 'string' ? event.info_request_text.trim() || null : null
}
export function auditAssignedStaffName(event: StaffAuditEvent): string | null {
  return event.action === 'application.manual_review' && savedId(event.assigned_staff_user_id)
    && typeof event.assigned_staff_display_name === 'string' ? event.assigned_staff_display_name.trim() || null : null
}
export function auditRequestedFields(event: StaffAuditEvent): string[] {
  if (!['staff.request_info', 'application.needs_info'].includes(event.action)) return []
  const labels: Record<string, string> = { purpose_details: 'Дополнительные сведения о цели кредита',
    investment_object: 'Объект инвестирования', collateral_type: 'Вид залога', collateral_value: 'Оценочная стоимость залога' }
  const fields = event.details.requested_fields
  return Array.isArray(fields) ? [...new Set(fields.filter((key): key is string => typeof key === 'string' && Object.hasOwn(labels, key)))].map((key) => labels[key]) : []
}
export function auditOfferTerms(event: StaffAuditEvent) {
  const terms = event.offer_terms
  if (!savedId(event.offer_id) || !terms || typeof terms !== 'object' || Array.isArray(terms)
    || terms.id !== undefined && terms.id !== event.offer_id || terms.offer_id !== undefined && terms.offer_id !== event.offer_id
    || typeof terms.product_code !== 'string' || !terms.product_code.trim()
    || numeric(terms.amount) === null || Number(terms.amount) <= 0
    || !Number.isSafeInteger(terms.term_months) || Number(terms.term_months) <= 0
    || numeric(terms.rate) === null || Number(terms.rate) < 0
    || numeric(terms.monthly_payment) === null || Number(terms.monthly_payment) < 0) return null
  return { id: event.offer_id, product: terms.product_code, amount: terms.amount, term: Number(terms.term_months),
    rate: terms.rate, payment: terms.monthly_payment }
}
export function auditReasons(event: StaffAuditEvent): string[] {
  const labels: Record<string, string> = {
    FNS_INACTIVE: 'Организация неактивна по данным проверки ФНС', BKI_STOP: 'В кредитной истории выявлен стоп-фактор',
    MISSING_DATA: 'Требуются дополнительные сведения', MISSING_INVESTMENT_DATA: 'Недостаточно сведений об инвестициях или залоге',
    OVERDUE_MANUAL: 'Просрочка требует ручного рассмотрения', AMOUNT_MANUAL: 'Сумма требует ручного рассмотрения',
    ORIGINAL_ELIGIBLE: 'Исходные условия соответствуют демонстрационным правилам',
    ALTERNATIVE_ELIGIBLE: 'Найдены допустимые демонстрационные условия',
    NO_AUTOMATIC_OFFER: 'Не удалось подобрать условия для автоматического решения',
    HIGH_RISK_NO_OFFER: 'Не найдены допустимые условия с учётом риска',
  }
  return Array.isArray(event.details.reason_codes) ? event.details.reason_codes.map((code) =>
    typeof code === 'string' ? labels[code] ?? 'Причина без текстового описания' : 'Причина без текстового описания') : []
}
function auditFilterAction(action: string) {
  const aliases: Record<string, string> = {
    'application.accepted': 'offer.accept',
    'staff.request_info': 'application.needs_info',
    'sign_demo.signed': 'application.signed_demo',
    'disbursement.demo': 'application.disbursed_demo',
    // The old combined approval/refusal filter has no single new equivalent.
    'staff.decision': 'all',
  }
  return Object.hasOwn(aliases, action) ? aliases[action] : action
}
export function auditActionKey(event: StaffAuditEvent): string {
  if (event.action === 'staff.decision' || event.action === 'workflow.policy') {
    const decision = event.details.decision
    if (typeof decision === 'string' && Object.hasOwn(decisionActions, decision)) return decisionActions[decision]
    const status = event.status_at_event ?? event.details.status
    return typeof status === 'string' && ['approved', 'rejected', 'counter_offer', 'needs_info', 'manual_review'].includes(status)
      ? `application.${status}` : 'other'
  }
  if (event.action === 'application.closed') {
    // Exact reasons emitted by the server; role/status alone cannot prove withdrawal.
    if (event.details.reason === 'Клиент отозвал заявку') return 'application.withdrawn'
    if (event.details.reason === 'Клиент отклонил предложение') return 'offer.decline'
  }
  const key = auditFilterAction(event.action)
  return Object.hasOwn(actionDictionary, key) || Object.hasOwn(extraActionLabels, key) ? key : 'other'
}
export function auditActionLabel(event: StaffAuditEvent) {
  return actionLabel(auditActionKey(event))
}
function matchesAuditActor(event: StaffAuditEvent, actor: string) {
  return actor === 'all' || (actor.startsWith('STAFF:')
    ? event.actor_role === 'STAFF' && String(event.actor_user_id) === actor.slice(6) : event.actor_role === actor)
}
function matchesAuditAction(event: StaffAuditEvent, action: string) {
  const key = auditFilterAction(action)
  return key === 'all' || auditActionKey(event) === key
}
function actorRole(actor: string) { return actor.startsWith('STAFF:') ? 'STAFF' : actor }
function supportedAuditPair(rows: AuditRow[], actor: string, action: string) {
  const key = auditFilterAction(action)
  return actor === 'all' || key === 'all'
    || Object.hasOwn(actionDictionary, key) && actionDictionary[key].roles.includes(actorRole(actor))
    || rows.some((row) => isVisibleAuditEvent(row) && matchesAuditActor(row, actor) && matchesAuditAction(row, key))
}
export function auditActionOptions(rows: AuditRow[], actor = 'all') {
  const keys = new Set(Object.keys(actionDictionary).filter((key) => supportedAuditPair(rows, actor, key)))
  for (const row of rows.filter(isVisibleAuditEvent)) {
    if (matchesAuditActor(row, actor)) keys.add(auditActionKey(row))
  }
  return [...keys].map((key) => ({ key, content: actionLabel(key) }))
}
export function auditActorOptions(rows: AuditRow[], action: string, session: Session) {
  const visible = rows.filter(isVisibleAuditEvent)
  const roles = [
    { key: 'SYSTEM', content: 'Система' }, { key: 'CLIENT', content: 'Клиенты' }, { key: 'STAFF', content: 'Все сотрудники' },
  ].filter((option) => supportedAuditPair(visible, option.key, action))
  const employeeIds = visible.filter((event) => event.actor_role === 'STAFF' && event.actor_user_id !== null)
    .map((event) => event.actor_user_id as number)
  if (session.user.role === 'STAFF') employeeIds.push(session.user.id)
  const employees = [...new Set(employeeIds)].sort((a, b) => a - b)
    .filter((id) => supportedAuditPair(visible, `STAFF:${id}`, action))
  return [{ key: 'all', content: 'Все инициаторы' }, ...roles, ...employees.map((id) => ({
    key: `STAFF:${id}`,
    content: visible.find((event) => event.actor_role === 'STAFF' && event.actor_user_id === id && event.actor_display_name?.trim())?.actor_display_name?.trim()
      || (id === session.user.id ? session.user.full_name.trim() : '') || `Сотрудник №${id}`,
  }))]
}
export function updateAuditFilters(rows: AuditRow[], current: AuditFilters, patch: Partial<AuditFilters>): AuditFilters {
  const next = { ...current, ...patch }
  next.action = auditFilterAction(next.action)
  if (!supportedAuditPair(rows, next.actor, next.action)) {
    if (patch.action !== undefined) next.actor = 'all'
    else if (patch.actor !== undefined) next.action = 'all'
  }
  return next
}
export function auditActorLabel(event: StaffAuditEvent) {
  if (event.actor_role === 'SYSTEM') return 'Система'
  const role = event.actor_role === 'STAFF' ? 'Сотрудник' : event.actor_role === 'CLIENT' ? 'Клиент' : 'Инициатор'
  return event.actor_user_id === null ? `${role} · ID не передан` : `${role} №${event.actor_user_id}`
}
export function auditActorPresentation(event: StaffAuditEvent, session: Session, companyName?: string | null) {
  if (event.actor_role === 'SYSTEM') return { role: 'Система', name: null }
  if (event.actor_role === 'STAFF') return { role: 'Сотрудник', name: event.actor_display_name?.trim() || (session.user.role === 'STAFF'
    && event.actor_user_id === session.user.id ? session.user.full_name.trim() || null : null) }
  return { role: event.actor_role === 'CLIENT' ? 'Клиент' : 'Инициатор',
    name: event.actor_role === 'CLIENT' ? clientActorCompany(event.company_name, companyName) : null }
}
export function auditHistoricalResult(event: StaffAuditEvent): string | null {
  if (event.result !== 'success') return null
  const status = event.status_at_event ?? event.details.status
  if (typeof status === 'string' && Object.hasOwn(staffStatusNames, status)) return staffStatusNames[status as keyof typeof staffStatusNames]
  const decision = event.details.decision
  return typeof decision === 'string' ? ({ approve: 'Одобрено', reject: 'Отказано', counter: 'Новое предложение',
    needs_info: 'Нужны сведения', manual_review: 'Ручное рассмотрение' } as Record<string, string>)[decision] ?? null : null
}
export function auditResultLabel(event: StaffAuditEvent) {
  if (event.action === 'sign_demo.invalid_code') return 'Код не подтверждён'
  const historical = auditHistoricalResult(event)
  if (historical) return historical
  return ({ success: 'Выполнено', error: 'Ошибка', denied: 'Доступ запрещён', failure: 'Не выполнено' } as Record<string, string>)[event.result] ?? 'Результат не распознан'
}
function calendarDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return ''
  const date = new Date(`${value}T00:00:00`)
  return Number.isFinite(date.getTime()) && localDay(date) === value ? value : ''
}
function localDay(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export function readAuditFilters(params: URLSearchParams): AuditFilters {
  const actor = params.get('actor') ?? 'all'
  return { q: params.get('q') ?? '', from: calendarDate(params.get('from') ?? ''), to: calendarDate(params.get('to') ?? ''),
    actor: /^(all|SYSTEM|CLIENT|STAFF|STAFF:\d+)$/.test(actor) ? actor : 'all',
    action: hiddenActions.has(params.get('action') ?? '') ? 'all' : auditFilterAction(params.get('action') || 'all'),
    sort: params.get('sort') === 'oldest' ? 'oldest' : 'newest' }
}
export function selectAuditRows(rows: AuditRow[], filters: AuditFilters) {
  const needle = filters.q.normalize('NFKC').trim().toLocaleLowerCase('ru-RU')
  return rows.filter(isVisibleAuditEvent).filter((row) => {
    const date = new Date(row.created_at)
    const day = Number.isFinite(date.getTime()) ? localDay(date) : ''
    return (!needle || (row.applicationNumber ?? '').normalize('NFKC').toLocaleLowerCase('ru-RU').includes(needle))
      && (!filters.from || !!day && day >= filters.from) && (!filters.to || !!day && day <= filters.to)
      && matchesAuditActor(row, filters.actor)
      && matchesAuditAction(row, filters.action)
  }).sort((a, b) => {
    const order = (Date.parse(a.created_at) || 0) - (Date.parse(b.created_at) || 0) || a.id - b.id
    return filters.sort === 'oldest' ? order : -order
  })
}
async function allPages<T extends { id: number }>(path: string, token: string, signal: AbortSignal) {
  const rows = new Map<number, T>()
  for (let offset = 0; offset <= 1_000_000; offset += 100) {
    const page = await staffGet<T[]>(`${path}?limit=100&offset=${offset}`, token, signal)
    if (!Array.isArray(page)) throw new Error('Не удалось прочитать журнал действий.')
    // Only repeated occurrences of the same audit ID are deduplicated across pages.
    // The API exposes no shared operation/status-event ID for merging distinct records.
    for (const row of page) rows.set(row.id, row)
    if (page.length < 100) return [...rows.values()]
  }
  throw new Error('Не удалось загрузить все страницы журнала.')
}
export async function loadAuditApplicationStatus(applicationId: number, token: string, signal: AbortSignal): Promise<string> {
  // Use the lightweight assigned-applications endpoint; a card GET would add a view audit event.
  for (let offset = 0; offset <= 1_000_000; offset += 100) {
    const page = await staffGet<StaffApplication[]>(`/applications?limit=100&offset=${offset}`, token, signal)
    if (!Array.isArray(page)) throw new Error('Не удалось прочитать текущий статус заявки.')
    const app = page.find((item) => item.id === applicationId)
    if (app) {
      if (!Object.hasOwn(staffStatusNames, app.status)) throw new Error('Текущий статус заявки не распознан.')
      return app.status
    }
    if (page.length < 100) break
  }
  throw new Error('Текущий статус заявки недоступен.')
}
export async function loadStaffAudit(token: string, signal: AbortSignal, knownCompanies: ReadonlyMap<number, string> = new Map()): Promise<AuditRow[]> {
  const [events, applications] = await Promise.all([
    allPages<StaffAuditEvent>('/audit', token, signal), allPages<StaffApplication>('/applications', token, signal),
  ])
  const numbers = new Map(applications.map((app) => [app.id, app.number]))
  const visible = events.filter(isVisibleAuditEvent)
  const clientApplications = new Set(visible.filter((event) => event.actor_role === 'CLIENT' && !event.company_name?.trim()).map((event) => event.application_id))
  const companies = new Map(knownCompanies)
  const representatives = new Map<number, StaffApplication>()
  for (const app of applications) {
    if (clientApplications.has(app.id) && !companies.get(app.company_id)?.trim()) representatives.set(app.company_id, app)
  }
  // Resolve a company once, using a related assigned application. Filters never refetch cards.
  const pending = [...representatives.values()]
  let next = 0
  async function worker() {
    while (next < pending.length) {
      const app = pending[next++]
      try {
        const detail = await staffGet<{ application: { id: number; company_id: number }; company: { id: number; name: string } }>(`/applications/${app.id}`, token, signal)
        if (detail.application?.id === app.id && detail.application.company_id === app.company_id
          && detail.company?.id === app.company_id && typeof detail.company.name === 'string' && detail.company.name.trim()) {
          companies.set(app.company_id, detail.company.name)
        }
      } catch (cause) {
        if (signal.aborted || cause instanceof Error && 'status' in cause && [401, 403].includes(Number(cause.status))) throw cause
        // A missing company name must not hide the event or substitute another company's name.
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, pending.length) }, () => worker()))
  const applicationCompanies = new Map(applications.map((app) => [app.id, app.company_id]))
  return visible.map((event) => {
    const companyId = event.application_id === null ? undefined : applicationCompanies.get(event.application_id)
    return { ...event, details: event.details && typeof event.details === 'object' && !Array.isArray(event.details) ? event.details : {},
      applicationNumber: event.application_id === null ? null : numbers.get(event.application_id) ?? null,
      companyName: event.actor_role === 'CLIENT' && companyId !== undefined ? event.company_name?.trim() || companies.get(companyId) || null : event.company_name?.trim() || null }
  })
}
