import { queryOptions } from '@tanstack/react-query'

export type Payment = {
  number: number
  payment_date: string
  payment_rub: number
  interest_rub: number
  principal_rub: number
  balance_rub: number
}
export type FixedContract = {
  fixed: true
  offer_id: number
  application_id: number
  borrower: { settlement_account: string }
  schedule: Payment[]
}

export class DocumentError extends Error {}

export function isCalendarDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00`)
  return Number.isFinite(date.getTime()) && date.getFullYear() === Number(value.slice(0, 4)) &&
    date.getMonth() + 1 === Number(value.slice(5, 7)) && date.getDate() === Number(value.slice(8, 10))
}

export function paymentDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString('ru-RU')
}

export function nextScheduledPayment(payments: Payment[], today = new Date()) {
  const day = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  return payments.reduce<Payment | undefined>((next, row) =>
    row.payment_date >= day && (!next || row.payment_date < next.payment_date) ? row : next,
  undefined)
}

export function validatePayments(value: unknown): asserts value is Payment[] {
  if (!Array.isArray(value) || !value.every((row) => row &&
    Number.isSafeInteger(row.number) && row.number > 0 &&
    typeof row.payment_date === 'string' && isCalendarDate(row.payment_date) &&
    [row.payment_rub, row.interest_rub, row.principal_rub, row.balance_rub]
      .every((amount) => Number.isSafeInteger(amount) && amount >= 0),
  )) throw new DocumentError('Не удалось прочитать график платежей. Попробуйте ещё раз.')
}

export async function fetchDocument(path: string, token: string, signal: AbortSignal) {
  const response = await fetch(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` }, signal, cache: 'no-store',
  })
  if (!response.ok) {
    throw new DocumentError(response.status === 401
      ? 'Сессия завершена. Выйдите и войдите снова.'
      : response.status === 404
        ? 'Документ пока недоступен. Попробуйте обновить его позже.'
        : 'Не удалось загрузить документ. Попробуйте ещё раз.')
  }
  return response
}

export function contractQueryOptions({ offerId, applicationId, userId, token, revision }: {
  offerId: number; applicationId: number; userId: number; token: string; revision: string
}) {
  return queryOptions({
    queryKey: ['accepted-contract', userId, applicationId, offerId, revision],
    queryFn: async ({ signal }) => {
      const data = await (await fetchDocument(`/offers/${offerId}/contract`, token, signal)).json() as FixedContract
      if (data.fixed !== true || data.offer_id !== offerId || data.application_id !== applicationId) {
        throw new DocumentError('Зафиксированный договор пока недоступен. Попробуйте позже.')
      }
      if (typeof data.borrower?.settlement_account !== 'string' || !/^\d{20}$/.test(data.borrower.settlement_account)) {
        throw new DocumentError('Не удалось прочитать счёт из договора. Попробуйте ещё раз.')
      }
      validatePayments(data.schedule)
      return data
    },
    staleTime: 30_000,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })
}

export async function loadFixedSchedule(offerId: number, token: string, signal: AbortSignal) {
  const data = await (await fetchDocument(`/offers/${offerId}/schedule`, token, signal)).json() as {
    fixed: boolean; offer_id: number; payments: Payment[]
  }
  if (data.fixed !== true || data.offer_id !== offerId) {
    throw new DocumentError('Зафиксированный график пока недоступен. Попробуйте позже.')
  }
  validatePayments(data.payments)
  return data.payments
}
