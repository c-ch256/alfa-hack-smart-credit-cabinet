export type StaffApplication = {
  id: number
  number: string
  company_id: number
  product_code: string
  amount: number
  status: string
  updated_at: string
}
export type StaffRow = StaffApplication & {
  company: { name: string; inn: string } | null
  riskBand: 'A' | 'B' | 'C' | 'D' | null
  receivedAdditionalInfo?: boolean
  detailUnavailable: boolean
  workCategories: StaffFilter[]
}
export const staffCategoryNames = {
  all: 'Все заявки',
  manual_review: 'Ручное рассмотрение',
  needs_info: 'Нужны сведения',
  waiting_offer: 'Ожидают ответа на предложение',
  staff_approved: 'Одобрены сотрудником',
  staff_rejected: 'Отказаны сотрудником',
  staff_offer_accepted: 'Предложение принято',
} as const
export type StaffFilter = keyof typeof staffCategoryNames
export type StaffSort = 'updated_desc' | 'updated_asc' | 'amount_desc' | 'amount_asc'
export type StaffRiskFilter = 'all' | 'none' | NonNullable<StaffRow['riskBand']>

// Подписи для исторических данных; актуальные продукты и лимиты загружаются из STAFF-каталога.
export const staffProductNames: Record<string, string> = {
  working_capital: 'Оборотный кредит',
  investment: 'Инвестиционный кредит',
}
export const staffStatusNames = {
  draft: 'Черновик', submitted: 'Отправлена', checking: 'На проверке',
  scoring: 'Оценка заявки', decision: 'Принятие решения',
  manual_review: 'Ручное рассмотрение', approved: 'Одобрено', rejected: 'Отказано',
  counter_offer: 'Новое предложение', needs_info: 'Нужны сведения',
  accepted: 'Предложение принято', signed_demo: 'Подписано (демо)',
  disbursed_demo: 'Кредит выдан (демо)', closed: 'Закрыта',
} as const

export type StaffListFilters = {
  status: StaffFilter
  q: string
  product: string
  risk: StaffRiskFilter
  sort: StaffSort
}
export const staffFilterDefaults: StaffListFilters = {
  status: 'manual_review', q: '', product: 'all', risk: 'all', sort: 'updated_desc',
}

export function readStaffFilters(params: URLSearchParams): StaffListFilters {
  const legacy = params.get('status')
  const legacyCategories: Record<string, StaffFilter> = {
    all: 'all', manual_review: 'manual_review', needs_info: 'needs_info',
    approved: 'staff_approved', rejected: 'staff_rejected', counter_offer: 'waiting_offer',
    accepted: 'staff_offer_accepted', signed_demo: 'staff_offer_accepted', disbursed_demo: 'staff_offer_accepted',
    draft: 'all', submitted: 'all', checking: 'all', scoring: 'all', decision: 'all', closed: 'all',
  }
  const category = params.get('category')
  const risk = params.get('risk')
  const sort = params.get('sort')
  return {
    status: category !== null && Object.hasOwn(staffCategoryNames, category)
      ? category as StaffFilter : legacy !== null && Object.hasOwn(legacyCategories, legacy)
        ? legacyCategories[legacy] : staffFilterDefaults.status,
    q: params.get('q') ?? '',
    product: params.get('product') || 'all',
    risk: risk !== null && ['all', 'none', 'A', 'B', 'C', 'D'].includes(risk)
      ? risk as StaffRiskFilter : staffFilterDefaults.risk,
    sort: sort !== null && ['updated_desc', 'updated_asc', 'amount_desc', 'amount_asc'].includes(sort)
      ? sort as StaffSort : staffFilterDefaults.sort,
  }
}

class StaffRequestError extends Error {
  status: number
  constructor(status: number) {
    super(status === 401 ? 'Сессия завершена. Войдите в кабинет заново.'
      : status === 403 ? 'Нет доступа к кабинету сотрудника.'
        : 'Не удалось загрузить заявки. Попробуйте ещё раз.')
    this.status = status
  }
}

async function get<T>(path: string, token: string, signal: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/staff${path}`, { headers: { Authorization: `Bearer ${token}` }, signal })
  } catch (cause) {
    if (signal.aborted) throw cause
    throw new Error('Нет соединения с сервером. Попробуйте ещё раз.', { cause })
  }
  if (!response.ok) throw new StaffRequestError(response.status)
  try {
    return await response.json() as T
  } catch {
    throw new Error('Не удалось прочитать данные заявок. Попробуйте ещё раз.')
  }
}

export { get as staffGet }

type StaffListApplication = StaffApplication & {
  company: { name: string; inn: string }
  risk_band: StaffRow['riskBand']
  received_additional_info: boolean
  work_categories: StaffFilter[]
}

export async function loadStaffApplications(token: string, signal: AbortSignal): Promise<StaffRow[]> {
  const applications = new Map<number, StaffRow>()
  for (let offset = 0; ; offset += 100) {
    const page = await get<StaffListApplication[]>(`/applications?limit=100&offset=${offset}`, token, signal)
    if (!Array.isArray(page)) throw new Error('Не удалось прочитать список заявок.')
    for (const application of page) {
      if (!Array.isArray(application.work_categories)
        || !application.work_categories.every((key) => key !== 'all' && Object.hasOwn(staffCategoryNames, key))) {
        throw new Error('Сервер не передал рабочие категории заявок. Повторите загрузку после обновления API.')
      }
      if (application.status === 'draft') continue
      applications.set(application.id, {
        ...application, company: application.company ?? null,
        riskBand: application.risk_band ?? null,
        receivedAdditionalInfo: application.received_additional_info === true,
        workCategories: application.work_categories,
        detailUnavailable: !application.company,
      })
    }
    if (page.length < 100) break
  }
  return [...applications.values()]
}

export function selectStaffRows(rows: StaffRow[], status: StaffFilter, search: string, product: string, sort: StaffSort, risk: StaffRiskFilter = 'all') {
  const needle = search.normalize('NFKC').trim().toLocaleLowerCase('ru-RU')
  return rows.filter((row) => row.status !== 'draft' && (status === 'all' || row.workCategories.includes(status)) &&
    (product === 'all' || row.product_code === product) &&
    (risk === 'all' || (risk === 'none' ? row.riskBand === null && !row.detailUnavailable : row.riskBand === risk)) &&
    (!needle || [row.number, row.company?.name ?? '', row.company?.inn ?? '']
      .some((value) => value.normalize('NFKC').toLocaleLowerCase('ru-RU').includes(needle))))
    .sort((a, b) => {
      const difference = sort.startsWith('amount') ? a.amount - b.amount
        : (Date.parse(a.updated_at) || 0) - (Date.parse(b.updated_at) || 0)
      return (sort.endsWith('desc') ? -difference : difference) || b.id - a.id
    })
}
