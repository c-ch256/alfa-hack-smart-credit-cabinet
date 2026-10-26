import StaffInfoResponse from './StaffInfoResponse'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import { InputDesktop } from '@alfalab/core-components/input/desktop'
import { SelectDesktop } from '@alfalab/core-components/select/desktop'
import type { Session } from './auth'
import StaffHeader, { StaffIcon } from './StaffHeader'
import StaffStatusHistory from './StaffStatusHistory'
import { dateLabel, loadStaffDetail, percent, rubles } from './staffApplicationDetail'
import type { StaffDetail } from './staffApplicationDetail'
import { staffProductNames, staffStatusNames } from './staffApplications'
import type { StaffRow } from './staffApplications'
import { auditActionLabel, auditActionOptions, auditActorOptions, auditActorPresentation, auditAssignedStaffName, auditDecisionLabel, auditDefaults, auditInfoResponse, auditInfoRequestText, auditInternalComment, auditOfferTerms, auditRequestedFields, auditReasons, auditResultLabel, isVisibleAuditEvent, loadAuditApplicationStatus, loadStaffAudit, readAuditFilters, selectAuditRows, updateAuditFilters } from './staffAudit'
import type { AuditFilters, AuditRow } from './staffAudit'
import './StaffAuditPage.css'

const filterDropdownProps = {
  optionsListWidth: 'field' as const,
  preventFlip: false,
  popperClassName: 'staff-audit-filter-popover',
  optionsListClassName: 'staff-audit-filter-options',
  optionClassName: 'staff-audit-filter-option',
  optionsListProps: { nativeScrollbar: true, scrollbarClassName: 'staff-audit-filter-scrollbar' },
  popoverProps: { preventOverflow: false, availableHeight: true, offset: [0, 0] as [number, number] },
}

function Actor({ event, session, companyName }: { event: AuditRow; session: Session; companyName?: string | null }) {
  const actor = auditActorPresentation(event, session, companyName)
  return <span className="staff-audit-actor"><span>{actor.role}</span>{actor.name && <span className="staff-audit-actor-name">{actor.name}</span>}</span>
}

function EventStatusHistory({ applicationId, session }: { applicationId: number; session: Session }) {
  const [open, setOpen] = useState(false)
  const history = useQuery({
    queryKey: ['staff-application', session.user.id, applicationId],
    queryFn: ({ signal }) => loadStaffDetail(applicationId, session.token, signal),
    enabled: open && session.user.role === 'STAFF',
    retry: false, gcTime: 0, staleTime: 30_000, refetchOnWindowFocus: false,
  })
  return <details className="staff-audit-status-history" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>История статусов<span aria-hidden="true" className="staff-audit-history-chevron" /></summary>
    {open && (history.isPending ? <p className="staff-audit-muted" role="status">Загружаем историю…</p>
      : history.isError ? <div className="staff-audit-history-error"><p className="staff-audit-muted" role="alert">Не удалось загрузить историю статусов.</p>
        <button type="button" className="staff-audit-status-retry" disabled={history.isFetching} onClick={() => void history.refetch()}>Повторить</button></div>
        : history.data.history.length ? <StaffStatusHistory history={history.data.history} compact />
          : <p className="staff-audit-muted">История статусов пока пуста.</p>)}
  </details>
}

function EventPanel({ event, session, companyName, returnUrl, onClose }: { event: AuditRow; session: Session; companyName: string | null; returnUrl: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const currentStatus = useQuery({
    queryKey: ['staff-audit-current-status', session.user.id, event.application_id],
    queryFn: ({ signal }) => loadAuditApplicationStatus(event.application_id!, session.token, signal),
    enabled: event.application_id !== null && session.user.role === 'STAFF',
    retry: false, gcTime: 0, staleTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: false,
  })
  const [copyStatus, setCopyStatus] = useState<'idle' | 'success' | 'error'>('idle')
  async function copyNumber() {
    if (!event.applicationNumber) return
    try {
      await navigator.clipboard.writeText(event.applicationNumber)
      setCopyStatus('success')
    } catch { setCopyStatus('error') }
  }
  useEffect(() => {
    const element = dialog.current
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    element?.showModal()
    return () => {
      element?.close()
      queueMicrotask(() => { if (previousFocus?.isConnected) previousFocus.focus() })
    }
  }, [])
  const details = event.details
  const result = auditResultLabel(event)
  const currentLabel = currentStatus.data ? staffStatusNames[currentStatus.data as keyof typeof staffStatusNames] : null
  const internalComment = auditInternalComment(event)
  const requestedFields = auditRequestedFields(event)
  const requestText = auditInfoRequestText(event)
  const infoResponse = auditInfoResponse(event)
  const assignedStaff = auditAssignedStaffName(event)
  const offer = auditOfferTerms(event)
  const accepted = ['offer.accept', 'application.accepted'].includes(event.action)
  const linkedApplication = useQuery({
    queryKey: ['staff-application', session.user.id, event.application_id],
    queryFn: ({ signal }) => loadStaffDetail(event.application_id!, session.token, signal),
    enabled: accepted && offer !== null && event.application_id !== null && session.user.role === 'STAFF',
    retry: false, staleTime: 30_000, gcTime: 0, refetchOnWindowFocus: false,
  })
  const acceptedOffer = linkedApplication.data?.application.id === event.application_id
    ? linkedApplication.data.offers.find((item) => item.id === offer?.id) : undefined
  const acceptedKind = acceptedOffer?.kind === 'original' ? 'Исходное предложение'
    : acceptedOffer && ['counter', 'alternative_product'].includes(acceptedOffer.kind) ? 'Изменённые условия банка' : null
  const systemDecision = event.actor_role === 'SYSTEM' && (auditDecisionLabel(event) !== null
    || ['application.approved', 'application.rejected', 'application.counter_offer', 'application.manual_review', 'application.needs_info'].includes(event.action))
  const reasons = systemDecision ? [...new Set([
    ...auditReasons(event).filter((reason) => reason !== 'Причина без текстового описания'),
    ...(typeof details.reason === 'string' && details.reason.trim() ? [details.reason.trim()] : []),
    ...(typeof details.rationale === 'string' ? [details.rationale.trim()] : Array.isArray(details.rationale)
      ? details.rationale.filter((value): value is string => typeof value === 'string').map((value) => value.trim()) : []),
  ].filter(Boolean))] : []
  const phase = typeof details.phase === 'string' ? ({ checking: 'Проверка сведений', scoring: 'Оценка риска', staff_assignment: 'Назначение сотрудника', scorer_configuration: 'Подготовка оценки риска' } as Record<string, string>)[details.phase] : null
  return <dialog ref={dialog} className="staff-audit-panel" aria-labelledby="staff-audit-event-title"
    onCancel={(e) => { e.preventDefault(); onClose() }} onClick={(e) => {
      if (e.target !== e.currentTarget) return
      const rect = e.currentTarget.getBoundingClientRect()
      if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) onClose()
    }}>
    <div className="staff-audit-panel-heading"><h2 id="staff-audit-event-title">{auditActionLabel(event)}</h2>
      <button type="button" className="staff-audit-close" onClick={onClose} aria-label="Закрыть подробности события" autoFocus>×</button></div>
    <div className="staff-audit-panel-body">
      <p className="staff-audit-event-date">{dateLabel(event.created_at)}</p>
      <div className="staff-audit-application">
        <span className="staff-audit-application-label">Номер заявки</span>
        {event.applicationNumber ? <div className="staff-audit-number-row">
          <span className="staff-number" title={event.applicationNumber} aria-label={event.applicationNumber}>
            <span className="staff-number-prefix">{event.applicationNumber.slice(0, -8)}</span><span>{event.applicationNumber.slice(-8)}</span>
          </span>
          <button type="button" className="staff-audit-copy" onClick={() => void copyNumber()} aria-label="Копировать полный номер заявки">
            {copyStatus === 'success' ? 'Скопировано' : 'Копировать'}
          </button>
        </div> : <span>{event.application_id === null ? 'Не указан' : 'Номер заявки не передан'}</span>}
        <span className={`staff-audit-copy-status${copyStatus === 'error' ? ' is-error' : ''}`} role="status">
          {copyStatus === 'error' ? 'Не удалось скопировать номер. Попробуйте ещё раз.' : copyStatus === 'success' ? 'Полный номер скопирован.' : ''}
        </span>
      </div>
      <dl className="staff-audit-event-pairs">
        <div><dt>Инициатор</dt><dd><Actor event={event} session={session} companyName={companyName} /></dd></div>
        <div><dt>Результат события</dt><dd><span className="staff-status-badge">{result}</span></dd></div>
        <div>
          <dt>Текущий статус заявки</dt><dd>{event.application_id === null ? <span className="staff-status-badge">—</span>
            : currentStatus.isPending ? <span className="staff-status-badge" role="status">Загружаем…</span>
            : currentStatus.isError ? <span className="staff-audit-status-state"><span className="staff-status-badge" role="alert">Не удалось загрузить</span>
              <button type="button" className="staff-audit-status-retry" disabled={currentStatus.isFetching}
                onClick={() => void currentStatus.refetch()}>Повторить</button></span>
              : <span className="staff-status-badge">{currentLabel ?? '—'}</span>}</dd>
        </div>
        {assignedStaff && <div><dt>Назначенный сотрудник</dt><dd>{assignedStaff}</dd></div>}
        {phase && <div><dt>Этап обработки</dt><dd>{phase}</dd></div>}
      </dl>
      {infoResponse && <section><h3>Клиент дополнил сведения</h3><StaffInfoResponse response={infoResponse} /></section>}
      {requestText && <section><h3>Запрос клиенту</h3><p>{requestText}</p></section>}
      {requestedFields.length > 0 && <section><h3>Запрошенные сведения</h3><ul>{requestedFields.map((field) => <li key={field}>{field}</li>)}</ul></section>}
      {offer && <section><h3>{accepted ? 'Принятые условия' : 'Условия предложения'}</h3>
        {accepted && acceptedKind && <p className="staff-audit-muted">{acceptedKind}</p>}
        <dl className="staff-audit-event-pairs">
          <div><dt>Продукт</dt><dd>{staffProductNames[offer.product] ?? offer.product}</dd></div>
          <div><dt>Сумма</dt><dd>{rubles(offer.amount)}</dd></div>
          <div><dt>Срок</dt><dd>{offer.term} мес.</dd></div>
          <div><dt>Ставка</dt><dd>{percent(offer.rate)} годовых</dd></div>
          <div><dt>Платёж в месяц</dt><dd>{rubles(offer.payment)}</dd></div>
        </dl>
        <p className="staff-audit-muted">На момент события</p>
      </section>}
      {internalComment && <section className="staff-audit-comment"><h3>Внутренний комментарий</h3><p>{internalComment}</p></section>}
      {reasons.length > 0 && <section><h3>Обоснование системы</h3><ul>{reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></section>}
      {event.application_id !== null && <EventStatusHistory applicationId={event.application_id} session={session} />}
    </div>
    {event.application_id !== null && <div className="staff-audit-panel-footer"><Link className="staff-audit-open" to={`/staff/applications/${event.application_id}`}
      state={{ staffAuditUrl: returnUrl }}>Открыть заявку</Link></div>}
  </dialog>
}

export default function StaffAuditPage({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const latestParams = useRef(params)
  const [selected, setSelected] = useState<AuditRow | null>(null)
  const view = readAuditFilters(params)
  const query = useQuery({ queryKey: ['staff-audit', session.user.id],
    queryFn: ({ signal }) => {
      const companies = new Map<number, string>()
      for (const app of queryClient.getQueryData<StaffRow[]>(['staff-applications', session.user.id]) ?? []) {
        if (app.company?.name) companies.set(app.company_id, app.company.name)
      }
      for (const [, detail] of queryClient.getQueriesData<StaffDetail>({ queryKey: ['staff-application', session.user.id] })) {
        if (detail?.company.name && detail.company.id === detail.application.company_id) companies.set(detail.company.id, detail.company.name)
      }
      return loadStaffAudit(session.token, signal, companies)
    }, enabled: session.user.role === 'STAFF',
    retry: false, gcTime: 0, staleTime: 30_000, refetchOnWindowFocus: false,
  })
  useLayoutEffect(() => { latestParams.current = params }, [params])
  useEffect(() => {
    if (!query.isSuccess) return
    // Old URLs may contain a pair that is no longer compatible. Keep its action.
    const current = readAuditFilters(latestParams.current)
    const compatible = updateAuditFilters(query.data, current, { action: current.action })
    if (compatible.actor === current.actor) return
    const next = new URLSearchParams(latestParams.current)
    next.delete('actor')
    latestParams.current = next
    setParams(next, { replace: true })
  }, [query.isSuccess, query.data, params, setParams])
  function update(patch: Partial<AuditFilters>) {
    const next = new URLSearchParams(latestParams.current)
    const values = updateAuditFilters(query.data ?? [], readAuditFilters(next), patch)
    for (const [key, value] of Object.entries(values)) {
      if (value === auditDefaults[key as keyof AuditFilters]) next.delete(key)
      else next.set(key, value)
    }
    latestParams.current = next
    setParams(next, { replace: true })
  }
  function reset() { update(auditDefaults) }
  const allRows = (query.data ?? []).filter(isVisibleAuditEvent)
  function companyNameFor(event: AuditRow) {
    if (event.actor_role !== 'CLIENT' || event.application_id === null) return null
    if (event.companyName) return event.companyName
    const loaded = queryClient.getQueryData<StaffRow[]>(['staff-applications', session.user.id])
      ?.find((app) => app.id === event.application_id)?.company?.name
    if (loaded) return loaded
    const detail = queryClient.getQueryData<StaffDetail>(['staff-application', session.user.id, event.application_id])
    return detail?.application.id === event.application_id && detail.company.id === detail.application.company_id ? detail.company.name : null
  }
  const rows = selectAuditRows(allRows, view)
  const actionOptions: { key: string; content: string; disabled?: boolean }[] = [
    { key: 'all', content: 'Все действия' }, ...auditActionOptions(allRows, view.actor),
  ]
  if (!actionOptions.some((item) => item.key === view.action)) actionOptions.push({ key: view.action, content: 'Выбранный тип действия', disabled: true })
  const actorOptions: { key: string; content: string; disabled?: boolean }[] = auditActorOptions(allRows, view.action, session)
  if (!actorOptions.some((item) => item.key === view.actor)) {
    const roleLabels: Record<string, string> = { SYSTEM: 'Система', CLIENT: 'Клиенты', STAFF: 'Все сотрудники' }
    actorOptions.push({ key: view.actor, disabled: true,
      content: roleLabels[view.actor] ?? `Сотрудник №${view.actor.slice(6)}` })
  }
  const invalidPeriod = !!view.from && !!view.to && view.from > view.to
  return <div className="staff-list-page staff-audit-page">
    <StaffHeader session={session} onLogout={onLogout} />
    <main className="staff-main">
      <div className="staff-heading"><h1>Журнал действий</h1></div>
      <section className="staff-filter-bar staff-audit-filters" aria-label="Фильтры журнала действий">
        <InputDesktop className="staff-search" size={40} block aria-label="Поиск по номеру заявки" placeholder="Номер заявки" value={view.q}
          leftAddons={<StaffIcon name="search" />} onChange={(_, { value }) => update({ q: value })} />
        <div className="staff-audit-period"><label>Период с<input type="date" value={view.from} aria-invalid={invalidPeriod} onChange={(e) => update({ from: e.target.value })} /></label>
          <label>по<input type="date" value={view.to} aria-invalid={invalidPeriod} onChange={(e) => update({ to: e.target.value })} /></label></div>
        <div className="staff-audit-select"><label className="visually-hidden" htmlFor="staff-audit-action-input">Тип действия</label>
          <SelectDesktop {...filterDropdownProps} id="staff-audit-action" block size={40} selected={view.action} options={actionOptions} allowUnselect={false} disabled={!query.isSuccess}
            fieldProps={{ className: 'staff-select-field' }} onChange={({ selected }) => update({ action: selected?.key ?? 'all' })} /></div>
        <div className="staff-audit-select"><label className="visually-hidden" htmlFor="staff-audit-actor-input">Инициатор</label>
          <SelectDesktop {...filterDropdownProps} id="staff-audit-actor" block size={40} selected={view.actor} options={actorOptions} allowUnselect={false} disabled={!query.isSuccess}
            fieldProps={{ className: 'staff-select-field' }} onChange={({ selected }) => update({ actor: selected?.key ?? 'all' })} /></div>
      </section>
      <div className="staff-filter-actions"><ButtonDesktop type="button" view="transparent" size={40} className="staff-reset" onClick={reset}>Сбросить</ButtonDesktop></div>
      {invalidPeriod && <p className="staff-audit-period-error" role="alert">Дата окончания периода должна быть не раньше даты начала.</p>}
      <section className="staff-results" aria-label="События журнала" aria-busy={query.isFetching}>
        <div className="staff-results-heading"><h2>Найдено событий: {query.data ? rows.length : '—'}</h2>
          <button type="button" className="staff-audit-sort" onClick={() => update({ sort: view.sort === 'newest' ? 'oldest' : 'newest' })}
            title={view.sort === 'newest' ? 'Показать сначала старые события' : 'Показать сначала новые события'}>
            {view.sort === 'newest' ? 'Сначала новые' : 'Сначала старые'}
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d={view.sort === 'newest' ? 'M8 3v10m-4-4 4 4 4-4' : 'M8 13V3m-4 4 4-4 4 4'} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
        {query.isPending ? <div className="staff-state" role="status"><span className="staff-loading" />Загружаем журнал…</div>
          : query.isError ? <div className="staff-state" role="alert"><h3>Не удалось загрузить журнал</h3><p>Проверьте соединение и повторите запрос. Если сессия завершена, войдите заново.</p>
            <ButtonDesktop type="button" view="secondary" size={40} loading={query.isFetching} onClick={() => void query.refetch()}>Повторить</ButtonDesktop></div>
            : !rows.length ? <div className="staff-state"><h3>{allRows.length ? 'События не найдены' : 'В журнале пока нет событий'}</h3>
              <p>{allRows.length ? 'Измените поиск или фильтры.' : 'Здесь появятся действия по назначенным вам заявкам.'}</p></div>
              : <div className="staff-table-scroll"><table className="staff-table staff-audit-table">
                <caption className="visually-hidden">События журнала, {view.sort === 'newest' ? 'новые' : 'старые'} сверху</caption>
                <colgroup><col className="staff-audit-col-date" /><col className="staff-audit-col-application" /><col className="staff-audit-col-actor" /><col className="staff-audit-col-action" /><col className="staff-audit-col-result" /></colgroup>
                <thead><tr><th scope="col">Дата и время</th><th scope="col">Заявка</th><th scope="col">Инициатор</th><th scope="col">Действие</th><th scope="col">Результат</th></tr></thead>
                <tbody>{rows.map((event) => <tr key={event.id} tabIndex={0} className="staff-clickable-row" aria-label={`Подробности: ${auditActionLabel(event)}, ${dateLabel(event.created_at)}`}
                  onClick={() => setSelected(event)} onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) { e.preventDefault(); setSelected(event) } }}>
                  <td className="staff-updated"><time dateTime={event.created_at}>{dateLabel(event.created_at)}</time></td>
                  <td><span className="staff-number" title={event.applicationNumber ?? undefined}>{event.applicationNumber
                    ? <><span className="staff-number-prefix">{event.applicationNumber.slice(0, -8)}</span><span>{event.applicationNumber.slice(-8)}</span></>
                    : event.application_id === null ? '—' : `Заявка №${event.application_id}`}</span></td>
                  <td><Actor event={event} session={session} companyName={companyNameFor(event)} /></td><td>{auditActionLabel(event)}</td><td><span className="staff-status-badge">{auditResultLabel(event)}</span></td>
                </tr>)}</tbody>
              </table></div>}
        {query.isSuccess && rows.length > 0 && <div className="staff-results-footer">Показано {rows.length} из {allRows.length}</div>}
      </section>
    </main>
    {selected && <EventPanel event={selected} session={session} companyName={companyNameFor(selected)} returnUrl={`${location.pathname}${location.search}`} onClose={() => setSelected(null)} />}
  </div>
}
