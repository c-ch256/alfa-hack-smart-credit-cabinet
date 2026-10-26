import { clientInfoResponses, sortedInfoResponses } from './staffAdditionalInfo'
import type { StaffDetail } from './staffApplicationDetail'

const supplementedFields = ['purpose_details', 'investment_object', 'collateral_type', 'collateral_value'] as const

// Prefer the saved first submission. Reversing changes is only a legacy fallback.
export function originalRequestDetails(detail: StaffDetail): Record<string, string | number | null> | null {
  if (detail.initial_request) return Object.fromEntries(
    supplementedFields.map((field) => [field, detail.initial_request![field]]),
  )
  const history = [...detail.history].sort((a, b) => a.sequence - b.sequence)
  if (!history.length || history.some((event, index) => event.sequence !== index + 1)
    || history.filter((event) => event.from_status === 'draft' && event.to_status === 'submitted').length !== 1) return null
  const replies = clientInfoResponses(history)
  const responses = sortedInfoResponses(detail.additional_info_responses).sort((a, b) => b.response_event_id - a.response_event_id)
  // Legacy replies without saved changes cannot be reversed safely.
  if (responses.length !== replies.length || new Set(responses.map((response) => response.response_event_id)).size !== responses.length) return null
  const original: Record<string, string | number | null> = Object.fromEntries(
    supplementedFields.map((field) => [field, detail.application[field]]),
  )
  for (const response of responses) {
    if (response.changes === null) return null
    if (new Set(response.changes.map((change) => change.field)).size !== response.changes.length) return null
    for (const change of response.changes) {
      if (change.field === 'message') continue
      if (!Object.hasOwn(original, change.field)) return null
      const same = change.field === 'collateral_value' && original[change.field] !== null && change.after !== null
        ? Number(original[change.field]) === Number(change.after) : original[change.field] === change.after
      if (!same) return null
      original[change.field] = change.before
    }
  }
  return original
}
