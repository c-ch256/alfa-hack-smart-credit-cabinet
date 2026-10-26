export type AdditionalInfoHistory = {
  sequence: number
  from_status: string | null
  to_status: string
  actor_role: string
  created_at: string
}

export function clientInfoResponses(history: readonly AdditionalInfoHistory[] = []) {
  return history.filter((event) => event.actor_role === 'CLIENT' && event.from_status === 'needs_info'
    && event.to_status === 'checking').sort((a, b) => b.sequence - a.sequence)
}

export function awaitingDecisionAfterInfo(status: string, history: readonly AdditionalInfoHistory[] = []) {
  if (status !== 'manual_review') return false
  const reply = clientInfoResponses(history)[0]
  if (!reply) return false
  const later = history.filter((event) => event.sequence > reply.sequence)
  const ended = new Set(['approved', 'rejected', 'counter_offer', 'accepted', 'signed_demo', 'disbursed_demo', 'closed', 'needs_info'])
  return later.some((event) => event.to_status === 'manual_review') && !later.some((event) => ended.has(event.to_status))
}

export const infoFieldNames: Record<string, string> = {
  purpose_details: 'Дополнительные сведения о цели кредита',
  investment_object: 'Объект инвестирования', collateral_type: 'Вид залога',
  collateral_value: 'Оценочная стоимость залога', message: 'Текст ответа',
}
export type SavedInfoResponse = {
  request_event_id: number | null
  response_event_id: number
  answered_at: string
  requested_fields: string[] | null
  changes: { field: string; before: string | number | null; after: string | number | null }[] | null
}
export function savedInfoResponse(value: unknown, eventId?: number): SavedInfoResponse | null {
  if (!value || typeof value !== 'object') return null
  const data = value as SavedInfoResponse
  const validId = (id: unknown) => typeof id === 'number' && Number.isSafeInteger(id) && id > 0
  const validValue = (v: unknown) => v === null || typeof v === 'string' || typeof v === 'number' && Number.isFinite(v)
  if (!validId(data.response_event_id) || eventId !== undefined && data.response_event_id !== eventId
    || data.request_event_id !== null && !validId(data.request_event_id)
    || typeof data.answered_at !== 'string' || !Number.isFinite(Date.parse(data.answered_at))
    || data.requested_fields !== null && (!Array.isArray(data.requested_fields)
      || !data.requested_fields.every((key) => typeof key === 'string' && key !== 'message' && Object.hasOwn(infoFieldNames, key)))
    || data.changes != null && (!Array.isArray(data.changes) || !data.changes.every((change) => !!change && typeof change.field === 'string'
      && Object.hasOwn(infoFieldNames, change.field) && validValue(change.before) && validValue(change.after)))) return null
  return { ...data, changes: data.changes ?? null }
}
export function sortedInfoResponses(values: unknown): SavedInfoResponse[] {
  return Array.isArray(values) ? values.map((value) => savedInfoResponse(value)).filter((value): value is SavedInfoResponse => value !== null)
    .sort((a, b) => Date.parse(b.answered_at) - Date.parse(a.answered_at) || b.response_event_id - a.response_event_id) : []
}
export function infoValue(field: string, value: string | number | null) {
  if (value === null || typeof value === 'string' && !value.trim()) return 'Не указано'
  if (field === 'collateral_value') {
    const amount = Number(value)
    if (Number.isFinite(amount)) return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(amount)
  }
  return String(value)
}
