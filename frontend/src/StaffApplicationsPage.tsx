import { useStaffProducts } from './staffProducts'
import { useLayoutEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import { InputDesktop } from '@alfalab/core-components/input/desktop'
import { SelectDesktop } from '@alfalab/core-components/select/desktop'
import type { Session } from './auth'
import StaffHeader, { StaffIcon } from './StaffHeader'
import { loadStaffApplications, readStaffFilters, selectStaffRows, staffFilterDefaults, staffStatusNames, staffCategoryNames } from './staffApplications'
import type { StaffFilter, StaffListFilters, StaffRiskFilter, StaffSort } from './staffApplications'
import './Dashboard.css'
import './StaffApplicationsPage.css'

const statusOptions = Object.entries(staffCategoryNames).map(([key, content]) => ({ key, content }))
// Keep only scroll coordinates in memory, separately for each employee and filter combination.
const listScrollPositions = new Map<string, { top: number; left: number; windowY: number }>()
const sortOptions: { key: StaffSort; content: string }[] = [
  { key: 'updated_desc', content: 'Сначала новые' }, { key: 'updated_asc', content: 'Сначала старые' },
  { key: 'amount_desc', content: 'Сумма: по убыванию' }, { key: 'amount_asc', content: 'Сумма: по возрастанию' },
]
const riskOptions: { key: StaffRiskFilter; content: string }[] = [
  { key: 'all', content: 'Все уровни риска' },
  { key: 'A', content: 'Риск A' }, { key: 'B', content: 'Риск B' },
  { key: 'C', content: 'Риск C' }, { key: 'D', content: 'Риск D' },
  { key: 'none', content: 'Нет оценки' },
]
const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 })

function UpdatedDate({ value }: { value: string }) {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return <>—</>
  return <time dateTime={value}>
    {date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })}{', '}
    {date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
  </time>
}

export default function StaffApplicationsPage({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const latestParams = useRef(params)
  const view = readStaffFilters(params)
  const { status, q: search, product, risk, sort } = view
  const catalog = useStaffProducts(session)
  const productNames = Object.fromEntries((catalog.data?.products ?? []).map((item) => [item.code, item.name]))
  const scrollKey = JSON.stringify([session.user.id, status, search, product, risk, sort])
  const tableScroll = useRef<HTMLDivElement>(null)
  const query = useQuery({
    queryKey: ['staff-applications', session.user.id],
    queryFn: ({ signal }) => loadStaffApplications(session.token, signal),
    staleTime: 30_000, gcTime: 0, retry: false, refetchOnWindowFocus: 'always',
    refetchInterval: (state) => state.state.data?.some((row) => row.status === 'needs_info') ? 5000 : false,
  })
  useLayoutEffect(() => {
    latestParams.current = params
    if (params.get('category') !== status || params.has('status')) {
      const next = new URLSearchParams(params)
      next.delete('status')
      next.set('category', status)
      latestParams.current = next
      setParams(next, { replace: true })
    }
  }, [params, setParams, status])
  function updateFilters(patch: Partial<StaffListFilters>) {
    // Router navigations can be deferred: merge rapid field edits with the last requested URL.
    const next = new URLSearchParams(latestParams.current)
    const values = { ...readStaffFilters(next), ...patch }
    next.delete('status')
    for (const [key, value] of Object.entries(values)) next.set(key === 'status' ? 'category' : key, value)
    latestParams.current = next
    setParams(next, { replace: true })
  }
  useLayoutEffect(() => {
    if (!query.isSuccess) return
    const saved = listScrollPositions.get(scrollKey)
    const table = tableScroll.current
    if (table) {
      table.scrollTop = saved?.top ?? 0
      table.scrollLeft = saved?.left ?? 0
    }
    window.scrollTo({ top: saved?.windowY ?? 0, behavior: 'instant' })
    function remember() {
      listScrollPositions.set(scrollKey, {
        top: table?.scrollTop ?? 0, left: table?.scrollLeft ?? 0, windowY: window.scrollY,
      })
    }
    table?.addEventListener('scroll', remember, { passive: true })
    window.addEventListener('scroll', remember, { passive: true })
    return () => {
      table?.removeEventListener('scroll', remember)
      window.removeEventListener('scroll', remember)
    }
  }, [query.isSuccess, scrollKey])


  const allRows = query.data ?? []
  const rows = selectStaffRows(allRows, status, search, product, sort, risk)
  useLayoutEffect(() => {
    const table = tableScroll.current?.querySelector('table')
    if (!table) return
    let active = true
    function alignRisk() {
      if (!active || !table) return
      const riskHeader = table.querySelector('th:nth-child(6)')
      const badges = [...table.querySelectorAll('.staff-status-badge')]
      const date = table.querySelector('.staff-updated time')
      if (!riskHeader || !badges.length || !date) return
      // One shared position keeps the header and all risk badges aligned.
      // Balance the visible gap after the widest displayed status badge.
      const statusRight = Math.max(...badges.map((badge) => badge.getBoundingClientRect().right))
      const dateLeft = date.getBoundingClientRect().left
      const header = riskHeader.getBoundingClientRect()
      const offset = (statusRight + dateLeft) / 2 - (header.left + header.width / 2)
      table.style.setProperty('--staff-risk-offset', `${offset}px`)
    }
    alignRisk()
    const observer = new ResizeObserver(alignRisk)
    observer.observe(table)
    void document.fonts.ready.then(alignRisk)
    return () => { active = false; observer.disconnect() }
  }, [query.data, query.isSuccess, scrollKey])
  const productOptions = [
    { key: 'all', content: 'Все продукты' },
    ...[...new Set([...Object.keys(productNames), ...allRows.map((row) => row.product_code)])]
      .map((key) => ({ key, content: productNames[key] ?? key })),
  ]
  const partial = allRows.some((row) => row.company === null || row.detailUnavailable)

  function openApplication(id: number) {
    navigate(`/staff/applications/${id}`, { state: { staffListUrl: location.pathname + location.search } })
  }

  function reset() {
    listScrollPositions.delete(JSON.stringify([session.user.id, 'manual_review', '', 'all', 'all', 'updated_desc']))
    updateFilters(staffFilterDefaults)
    if (tableScroll.current) tableScroll.current.scrollTo({ top: 0, left: 0, behavior: 'instant' })
  }

  return <div className="staff-list-page">
    <StaffHeader session={session} onLogout={onLogout} applicationsHref={location.pathname + location.search} />
    <main className="staff-main">
      <div className="staff-heading">
        <h1>Кредитные заявки</h1>
        <p>{status === 'manual_review' ? 'Заявки, требующие решения сотрудника' : 'Все доступные отправленные заявки'}</p>
      </div>
      <section className="staff-filter-bar" aria-label="Поиск и фильтры">
        <InputDesktop className="staff-search" block size={40} value={search}
          aria-label="Поиск по номеру заявки, компании или ИНН" placeholder="Компания, ИНН или № заявки"
          leftAddons={<StaffIcon name="search" />} onChange={(_, { value }) => updateFilters({ q: value })} />
        <div className="staff-status-filter">
          <label id="staff-status-label" htmlFor="staff-status-input" className="visually-hidden">Категория заявок</label>
          <SelectDesktop id="staff-status" block size={40} options={statusOptions} selected={status}
            allowUnselect={false} fieldProps={{ className: 'staff-select-field' }}
            onChange={({ selected }) => updateFilters({ status: (selected?.key ?? 'manual_review') as StaffFilter })} />
        </div>
        <div className="staff-product-filter">
          <label id="staff-product-label" htmlFor="staff-product-input" className="visually-hidden">Продукт</label>
          <SelectDesktop id="staff-product" block size={40} options={productOptions} selected={product}
            allowUnselect={false} fieldProps={{ className: 'staff-select-field' }}
            onChange={({ selected }) => updateFilters({ product: selected?.key ?? 'all' })} />
        </div>
        <div className="staff-risk-filter">
          <label id="staff-risk-label" htmlFor="staff-risk-input" className="visually-hidden">Уровень риска</label>
          <SelectDesktop id="staff-risk" block size={40} options={riskOptions} selected={risk}
            allowUnselect={false} fieldProps={{ className: 'staff-select-field' }}
            onChange={({ selected }) => updateFilters({ risk: (selected?.key ?? 'all') as StaffRiskFilter })} />
        </div>
        <div className="staff-sort-filter">
          <label id="staff-sort-label" htmlFor="staff-sort-input" className="visually-hidden">Сортировка заявок</label>
          <SelectDesktop id="staff-sort" block size={40} options={sortOptions} selected={sort}
            allowUnselect={false} fieldProps={{ className: 'staff-select-field' }}
            onChange={({ selected }) => updateFilters({ sort: (selected?.key ?? 'updated_desc') as StaffSort })} />
        </div>
      </section>
      <div className="staff-filter-actions">
        <ButtonDesktop type="button" view="text" size={40} className="staff-reset" onClick={reset}>Сбросить</ButtonDesktop>
      </div>
      {partial && !query.isError && <div className="staff-partial-error" role="alert">
        <span>Часть данных компаний или оценок риска недоступна. Поиск по названию и ИНН может быть неполным.</span>
        <button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>Повторить загрузку</button>
      </div>}
      <section className="staff-results" aria-label="Кредитные заявки сотрудника" aria-busy={query.isFetching}>
        <div className="staff-results-heading">
          <h2>Найдено заявок: {query.data ? rows.length : '—'}</h2>
        </div>
        {query.isPending ? <div className="staff-state" role="status"><span className="staff-loading" />Загружаем заявки…</div>
          : query.isError ? <div className="staff-state" role="alert">
            <h3>Не удалось загрузить заявки</h3><p>{query.error.message}</p>
            <ButtonDesktop type="button" view="secondary" size={40} loading={query.isFetching}
              onClick={() => void query.refetch()}>Повторить</ButtonDesktop>
          </div> : rows.length === 0 ? <div className="staff-state">
            <h3>{allRows.length ? 'По вашему запросу ничего не найдено' : 'Отправленных заявок пока нет'}</h3>
            <p>{allRows.length ? 'Измените поисковый запрос или выберите другие фильтры.' : 'Отправленные заявки появятся здесь.'}</p>
            {allRows.length > 0 && <ButtonDesktop type="button" view="secondary" size={40} onClick={reset}>Сбросить фильтры</ButtonDesktop>}
          </div> : <div className="staff-table-scroll" ref={tableScroll}>
            <table className="staff-table">
              <caption className="visually-hidden">Все страницы доступных отправленных заявок с выбранными фильтрами</caption>
              <colgroup><col className="staff-col-number" /><col className="staff-col-company" /><col className="staff-col-product" />
                <col className="staff-col-amount" /><col className="staff-col-status" /><col className="staff-col-risk" /><col className="staff-col-date" /></colgroup>
              <thead><tr><th scope="col">№ заявки</th><th scope="col">Компания / ИНН</th><th scope="col">Продукт</th>
                <th scope="col">Сумма</th><th scope="col">Статус</th><th scope="col"><span className="staff-risk-content">Риск</span></th><th scope="col">Обновлено</th></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.id} className="staff-clickable-row" tabIndex={0}
                aria-label={`Открыть заявку ${row.number}`} onClick={() => openApplication(row.id)}
                onKeyDown={(event) => { if (event.key === 'Enter' && event.target === event.currentTarget) { event.preventDefault(); openApplication(row.id) } }}>
                <td className="staff-application-number"><span className="staff-number" title={row.number}>
                  <span className="staff-number-prefix">{row.number.slice(0, -8)}</span><span>{row.number.slice(-8)}</span>
                </span></td>
                <td><span className="staff-company-name">{row.company?.name ?? 'Данные компании недоступны'}</span>
                  <span className="staff-company-inn">{row.company ? `ИНН ${row.company.inn}` : 'ИНН недоступен'}</span></td>
                <td>{productNames[row.product_code] ?? row.product_code}</td>
                <td className="staff-amount">{money.format(row.amount)}</td>
                <td><span className="staff-status-badge">{staffStatusNames[row.status as keyof typeof staffStatusNames] ?? 'Статус не определён'}</span>
                  {row.status === 'manual_review' && row.receivedAdditionalInfo && <span className="staff-info-received">Получены дополнительные сведения</span>}</td>
                <td className="staff-risk"><span className="staff-risk-content">{row.riskBand
                  ? <span className={`staff-risk-badge staff-risk-${row.riskBand}`}>{row.riskBand}</span> : '—'}</span></td>
                <td className="staff-updated"><UpdatedDate value={row.updated_at} /></td>
              </tr>)}</tbody>
            </table>
          </div>}
        {query.data && !query.isError && <div className="staff-results-footer">Показано {rows.length} из {allRows.length}</div>}
      </section>
    </main>
  </div>
}
