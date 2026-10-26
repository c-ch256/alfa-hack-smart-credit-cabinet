import { useQuery } from '@tanstack/react-query'
import type { Session } from './auth'
import { staffGet } from './staffApplications'

export type StaffProduct = {
  code: string; name: string; terms_version: string
  min_amount: number; max_amount: number; min_term_months: number; max_term_months: number
  margin_pp: number | string
}
export type StaffCatalog = { products: StaffProduct[]; demonstration: boolean }
export function validStaffProduct(product: StaffProduct) {
  return !!product && typeof product.code === 'string' && !!product.code && typeof product.name === 'string'
    && [product.min_amount, product.max_amount, product.min_term_months, product.max_term_months].every((n) => Number.isSafeInteger(n) && n > 0)
    && product.min_amount <= product.max_amount && product.min_term_months <= product.max_term_months
}
export async function loadStaffProducts(token: string, signal: AbortSignal): Promise<StaffCatalog> {
  const data = await staffGet<StaffCatalog>('/products', token, signal)
  if (!data || !Array.isArray(data.products) || !data.products.every(validStaffProduct)
    || new Set(data.products.map((p) => p.code)).size !== data.products.length) {
    throw new Error('Не удалось прочитать каталог продуктов. Повторите запрос.')
  }
  return data
}
export function useStaffProducts(session: Session) {
  return useQuery({ queryKey: ['staff-products', session.user.id],
    queryFn: ({ signal }) => loadStaffProducts(session.token, signal), enabled: session.user.role === 'STAFF',
    retry: false, staleTime: 60_000, refetchOnWindowFocus: false })
}
