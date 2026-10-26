export const additionalInfoFields = {
  purpose_details: { label: 'Подробности цели кредита', maxLength: 2000 },
  investment_object: { label: 'Объект инвестирования', maxLength: 255 },
  collateral_type: { label: 'Вид залога', maxLength: 128 },
  collateral_value: { label: 'Оценочная стоимость залога', maxLength: 24 },
} as const
export type InfoField = keyof typeof additionalInfoFields
export type InfoApplication = {
  version: number
  product_code: string
  purpose_details: string | null
  investment_object: string | null
  collateral_type: string | null
  collateral_value: number | null
  active_info_request?: { request_event_id: number; fields: string[] } | null
}
export type InfoValues = Record<InfoField, string>
export type InfoPayload = { version: number; request_event_id: number } & Partial<Record<InfoField, string | number>>

export function requestedInfoFields(application: InfoApplication): InfoField[] | null {
  const request = application.active_info_request
  if (!request || !Number.isSafeInteger(request.request_event_id) || request.request_event_id <= 0
    || !Array.isArray(request.fields) || !request.fields.length
    || new Set(request.fields).size !== request.fields.length
    || !request.fields.every((field) => Object.hasOwn(additionalInfoFields, field)
      && (field === 'purpose_details' || application.product_code === 'investment'))) return null
  // A stable field order also keeps sequential requests visually consistent.
  return (Object.keys(additionalInfoFields) as InfoField[]).filter((field) => request.fields.includes(field))
}
export function initialInfoValues(): InfoValues {
  return { purpose_details: '', investment_object: '', collateral_type: '', collateral_value: '' }
}
export function infoPayload(application: InfoApplication, values: InfoValues): InfoPayload {
  const fields = requestedInfoFields(application)
  if (!fields || !application.active_info_request) throw new Error('Запрошенные сведения недоступны. Обновите страницу.')
  const payload: InfoPayload = { version: application.version, request_event_id: application.active_info_request.request_event_id }
  for (const field of fields) {
    const value = values[field].trim()
    if (!value || value.length > additionalInfoFields[field].maxLength) {
      throw new Error(`Заполните поле «${additionalInfoFields[field].label}».`)
    }
    if (field === 'collateral_value') {
      const amount = Number(value)
      if (!/^\d+$/.test(value) || !Number.isSafeInteger(amount) || amount <= 0) {
        throw new Error('Укажите оценочную стоимость залога целым числом больше нуля.')
      }
      payload[field] = amount
    } else payload[field] = value
  }
  return payload
}
