import { infoFieldNames, infoValue, sortedInfoResponses } from './staffAdditionalInfo'
import { originalRequestDetails } from './staffOriginalRequest'
import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useParams } from 'react-router-dom'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import type { Session } from './auth'
import StaffHeader from './StaffHeader'
import StaffDecisionForm from './StaffDecisionForm'
import StaffStatusHistory from './StaffStatusHistory'
import { presentCheck } from './staffDecision'
import { staffProductNames } from './staffApplications'
import {
  actorLabel, currentBankOffer, dateLabel, decimal, decisionEvent, loadStaffDetail,
  newestHistory, numeric, percent, rubles, scoreDscr, staffDecisionComment, StaffDetailError, statusLabel,
} from './staffApplicationDetail'
import type { StaffCheck, StaffDetail, StaffOffer } from './staffApplicationDetail'
import './StaffApplicationPage.css'
import './StaffInfoResponse.css'

type Pair = [string, ReactNode]
function text(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : 'Нет данных'
}
function productName(code: string): string { return staffProductNames[code] ?? code }
function Pairs({ items }: { items: Pair[] }) {
  return <dl className="staff-detail-pairs">{items.map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
}
function Accordion({ title, children }: { title: string; children: ReactNode }) {
  return <details className="staff-detail-card staff-detail-accordion">
    <summary>{title}<span aria-hidden="true" className="staff-detail-chevron" /></summary>
    <div className="staff-detail-accordion-body">{children}</div>
  </details>
}

function checkPairs(check: StaffCheck): Pair[] {
  const data = check.payload ?? {}
  const yesNo = (value: unknown) => typeof value === 'boolean' ? value ? 'Да' : 'Нет' : 'Нет данных'
  if (data.available === false) return [['Сведения', 'Недоступны'], ['Дата проверки', dateLabel(check.created_at)]]
  if (check.source === 'FNS') return [
    ['Статус организации', ({ active: 'Действующая', liquidating: 'В процессе ликвидации', inactive: 'Не действует' } as Record<string, string>)[String(data.status)] ?? 'Нет данных'],
    ['Налоговая задолженность', yesNo(data.tax_debt)], ['Массовый адрес', yesNo(data.mass_address)],
    ['Дата проверки', dateLabel(check.created_at)],
  ]
  if (check.source === 'BKI') return [
    ['Действующие кредиты', decimal(data.active_loans, 0)], ['Общая задолженность', rubles(data.total_debt)],
    ['Максимальная просрочка за 12 месяцев, дней', decimal(data.max_overdue_days_12m, 0)],
    ['Дефолты', decimal(data.defaults, 0)], ['Дата проверки', dateLabel(check.created_at)],
  ]
  return []
}
function CheckIcon({ tone }: { tone: 'good' | 'warning' | 'neutral' }) {
  return <span className={`staff-check-icon staff-check-icon-${tone}`} aria-label={tone === 'good' ? 'Без замечаний' : tone === 'warning' ? 'Есть замечания' : 'Нет результата'}>
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />{tone === 'good' ? <path d="m7.5 12 3 3 6-6" /> : tone === 'warning' ? <path d="M12 7v6m0 4h.01" /> : <path d="M8 12h8" />}
    </svg>
  </span>
}
function Checks({ checks }: { checks: StaffCheck[] }) {
  return <section className="staff-detail-card">
    <h2>Результаты проверок</h2>
    {['FNS', 'BKI'].map((source) => {
      const check = checks.find((item) => item.source === source)
      const name = source === 'FNS' ? 'ФНС' : 'БКИ'
      const result = presentCheck(check)
      return check ? <details className="staff-detail-check" key={source}>
        <summary><CheckIcon tone={result.tone} /><span className="staff-detail-check-copy"><strong>{name}</strong></span>
          <span className="staff-detail-chevron" aria-hidden="true" /></summary>
        <Pairs items={checkPairs(check)} />
      </details> : <div className="staff-detail-check-missing" key={source}><CheckIcon tone="neutral" />
        <span className="staff-detail-check-copy"><strong>{name}</strong></span></div>
    })}
  </section>
}
function CreditRisk({ detail }: { detail: StaffDetail }) {
  const score = detail.score
  const ready = score?.result_status === 'success'
  const band = score?.risk_band
  const dscr = scoreDscr(detail)
  return <section className="staff-detail-card">
    <div className="staff-detail-card-heading staff-detail-risk-heading"><div><h2>Оценка кредитного риска</h2>
      {score && <p className="staff-detail-risk-date">Оценка от {dateLabel(score.created_at)}</p>}</div>
      {score?.is_test_result && <span className="staff-status-badge">Тестовый результат</span>}</div>
    {!score ? <p className="staff-detail-muted">Оценка риска пока не передана.</p>
      : !ready ? <p className="staff-detail-muted">Оценка риска недоступна. Технический сбой не означает отказ.</p> : null}
    <div className="staff-detail-risk-metrics">
      <div><span>Вероятность дефолта · PD</span><strong>{ready && numeric(score?.pd) !== null ? `${new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(score?.pd) * 100)}%` : 'Нет данных'}</strong></div>
      <div><span>Категория риска</span>{ready && band && ['A', 'B', 'C', 'D'].includes(band)
        ? <strong className={`staff-risk-badge staff-risk-${band}`}>{band}</strong> : <strong>Нет данных</strong>}</div>
      <div><span>DSCR</span><strong>{ready && numeric(dscr?.value) !== null
        ? new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(dscr?.value)) : '—'}</strong></div>
    </div>
    {numeric(dscr?.value) === null && <p className="staff-detail-muted">DSCR: {dscr?.explanation || 'Расчёт для показанных условий недоступен.'}</p>}
    <details className="staff-detail-factors-disclosure">
      <summary>Факторы оценки<span className="staff-detail-chevron" aria-hidden="true" /></summary>
    {ready && score?.reasons?.length ? <ul className="staff-detail-factors">{score.reasons.map((reason, index) =>
      <li key={`${reason.feature}-${index}`}><p>{text(reason.text)}</p>
        <span>{({ increases_risk: 'Повышает риск', decreases_risk: 'Снижает риск' } as Record<string, string>)[reason.effect] ?? 'Направление влияния не передано'}
          {numeric(reason.contribution) !== null && <> · Вклад: {decimal(reason.contribution, 6)}</>}</span></li>)}</ul>
      : <p className="staff-detail-muted">Факторы оценки не переданы.</p>}
    </details>
  </section>
}
function CompanyData({ detail }: { detail: StaffDetail }) {
  const c = detail.company
  const snapshot = detail.company_snapshot
  return <Accordion title="Данные компании">
    <h3>Сведения о компании</h3>
    <Pairs items={[
      ['Название', text(c.name)], ['ИНН', text(c.inn)], ['Форма организации', ({ OOO: 'ООО', IP: 'ИП' } as Record<string, string>)[c.legal_form] ?? text(c.legal_form)],
      [c.legal_form === 'IP' ? 'ОГРНИП' : 'ОГРН', text(c.registration_number)], ['Юридический адрес', text(c.legal_address)],
      ['ОКВЭД', text(c.okved)], ['Отрасль', text(c.industry)], ['Регион', text(c.region)],
      ['Дата регистрации', dateLabel(c.registered_at, false)], ['Клиент банка с', dateLabel(c.bank_client_since, false)],
    ]} />
    <h3>Банковские реквизиты</h3><p className="staff-detail-muted">Демонстрационные реквизиты из текущего профиля компании</p>
    <Pairs items={[
      ['Расчётный счёт', text(c.settlement_account)], ['Банк', text(c.bank_name)],
      ['БИК', text(c.bank_bik)], ['Корреспондентский счёт', text(c.correspondent_account)],
    ]} />
    <h3>Финансовые показатели</h3>
    <Pairs items={[
      ['Среднемесячная выручка', rubles(snapshot.avg_monthly_revenue)],
      ['Среднемесячные расходы', rubles(snapshot.avg_monthly_expenses)],
      ['Изменение выручки за 6 месяцев', percent(snapshot.revenue_trend_6m, true)],
      ['Платежи по действующим обязательствам в месяц', rubles(snapshot.existing_monthly_payments)],
      ['Возраст бизнеса, месяцев', decimal(snapshot.business_age_months, 0)],
      ['Срок обслуживания в банке, месяцев', decimal(snapshot.bank_client_months, 0)],
    ]} />
  </Accordion>
}
function ClientAdditions({ detail }: { detail: StaffDetail }) {
  const responses = sortedInfoResponses(detail.additional_info_responses)
  if (!responses.length) return null
  return <section className="staff-detail-card"><h2>Клиент дополнил сведения</h2><div className="staff-info-responses">
    {responses.map((response) => <div className="staff-info-response" key={response.response_event_id}>
      <p className="staff-info-answer-date">Дата ответа: <time dateTime={response.answered_at}>{dateLabel(response.answered_at)}</time></p>
      {response.changes === null ? <p className="staff-detail-muted">Состав изменений не сохранён</p> : <>
        <Pairs items={response.changes.map((change) => [infoFieldNames[change.field], infoValue(change.field, change.after)])} />
        {!response.changes.length && <p className="staff-detail-muted">Значения полей не изменились.</p>}
      </>}
    </div>)}
  </div></section>
}
function ApplicationHistory({ detail, session }: { detail: StaffDetail; session: Session }) {
  const history = detail.history
  return <Accordion title="История заявки">
    {!history.length ? <p className="staff-detail-muted">История событий не передана.</p>
      : <StaffStatusHistory history={history} user={session.user} companyName={detail.company.name} />}
  </Accordion>
}
function OfferTerms({ offer, accepted }: { offer: StaffOffer; accepted: boolean }) {
  return <div className="staff-detail-bank-terms">
    <h3>{accepted ? 'Согласованные условия банка' : 'Условия банка'}</h3>
    <Pairs items={[
      ['Продукт', productName(offer.product_code)], ['Сумма', rubles(offer.amount)],
      ['Срок', `${offer.term_months} мес.`], ['Ставка', `${percent(offer.rate)} годовых`],
      ['Ежемесячный платёж', rubles(offer.monthly_payment)], ['Дата предложения', dateLabel(offer.created_at)],
      ['Состояние предложения', ({ proposed: 'Ожидает ответа клиента', accepted: 'Принято клиентом', declined: 'Отклонено клиентом' } as Record<string, string>)[offer.status] ?? 'Не передано'],
    ]} />
  </div>
}
const stateCopy: Record<string, [string, string]> = {
  draft: ['Черновик', 'Клиент ещё не отправил заявку.'],
  submitted: ['Заявка отправлена', 'Ожидается обработка заявки.'],
  checking: ['Проверка заявки', 'Выполняются проверки сведений.'],
  scoring: ['Оценка риска', 'Выполняется оценка кредитного риска.'],
  decision: ['Принятие решения', 'Решение по заявке ещё не завершено.'],
  manual_review: ['Требуется решение сотрудника', 'Заявка находится на ручном рассмотрении.'],
  approved: ['Заявка одобрена', 'Ожидается ответ клиента на предложение банка.'],
  rejected: ['В кредите отказано', 'Рассмотрение заявки завершено отказом.'],
  counter_offer: ['Новое предложение', 'Банк предложил другие условия. Ожидается ответ клиента.'],
  needs_info: ['Нужны сведения', 'Клиенту направлен запрос дополнительных сведений.'],
  accepted: ['Предложение принято', 'Клиент принял предложение. Демо-подписание ещё не завершено.'],
  signed_demo: ['Договор подписан · демо', 'Подписание подтверждено в демонстрационном режиме.'],
  disbursed_demo: ['Кредит выдан · демо', 'Выдача выполнена в демонстрационном режиме. Это не реальный перевод средств.'],
  closed: ['Заявка закрыта', 'Оформление этой заявки прекращено.'],
}
function ApplicationState({ detail, session }: { detail: StaffDetail; session: Session }) {
  const app = detail.application
  const offer = currentBankOffer(detail)
  const decision = decisionEvent(detail)
  const internalComment = staffDecisionComment(detail)
  const copy = stateCopy[app.status] ?? ['Состояние заявки', 'Описание состояния не передано.']
  const relevantDecision = ['approved', 'rejected', 'counter_offer', 'accepted', 'signed_demo', 'disbursed_demo', 'closed'].includes(app.status)
  const stateEvent = newestHistory(detail.history).find((event) => event.to_status === app.status)
  return <aside className="staff-detail-sidebar" aria-label="Состояние заявки">
    <section className="staff-detail-card">
      <h2>Состояние заявки</h2><div className="staff-detail-state-copy"><h3>{copy[0]}</h3><p>{copy[1]}</p></div>
      {stateEvent && <Pairs items={[
        ['Дата события', dateLabel(stateEvent.created_at)], ['Инициатор', actorLabel(stateEvent, session.user, detail.company.name)],
      ]} />}
      {stateEvent?.reason && <div className="staff-detail-note"><h3>Комментарий события</h3><p>{stateEvent.reason}</p></div>}
      {relevantDecision && <div className="staff-detail-decision">
        <h3>Решение банка</h3><Pairs items={[
          ['Источник решения', decision ? actorLabel(decision, session.user, detail.company.name) : 'Не передан'],
          ...(decision ? [['Дата решения', dateLabel(decision.created_at)] as Pair] : []),
        ]} />
        {internalComment && <><h3>Внутренний комментарий сотрудника</h3><p>{internalComment}</p></>}
      </div>}
      {offer && <OfferTerms offer={offer} accepted={offer.status === 'accepted'} />}
      {['approved', 'counter_offer', 'accepted', 'signed_demo', 'disbursed_demo'].includes(app.status) && !offer &&
        <p className="staff-detail-muted">Актуальные условия предложения не переданы.</p>}
      {(app.info_request?.trim() || app.info_response?.trim()) && <div className="staff-detail-info-request">
        {app.info_request?.trim() && <><h3>Запрос клиенту</h3><p>{app.info_request}</p></>}
        {app.info_response?.trim() && <><h3>Ответ клиента</h3><p>{app.info_response}</p></>}
      </div>}
      {['signed_demo', 'disbursed_demo'].includes(app.status) && <Pairs items={[
        ...['signed_demo', 'disbursed_demo'].flatMap((status): Pair[] => {
          const event = newestHistory(detail.history).find((item) => item.to_status === status)
          return event ? [[status === 'signed_demo' ? 'Демо-подписание' : 'Демо-выдача', dateLabel(event.created_at)]] : []
        }),
      ]} />}
    </section>
  </aside>
}

export default function StaffApplicationPage({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const { id: rawId } = useParams()
  const queryClient = useQueryClient()
  const location = useLocation()
  const id = rawId && /^\d+$/.test(rawId) ? Number(rawId) : NaN
  const valid = Number.isSafeInteger(id) && id > 0 && id <= 2_147_483_647
  const source = (location.state as { staffListUrl?: unknown } | null)?.staffListUrl
  const applicationsBack = typeof source === 'string' && (source === '/staff/applications' || source.startsWith('/staff/applications?'))
    ? source : '/staff/applications'
  const auditSource = (location.state as { staffAuditUrl?: unknown } | null)?.staffAuditUrl
  const auditBack = typeof auditSource === 'string' && (auditSource === '/staff/audit' || auditSource.startsWith('/staff/audit?'))
    ? auditSource : undefined
  const back = auditBack ?? applicationsBack
  const query = useQuery({
    queryKey: ['staff-application', session.user.id, id],
    queryFn: ({ signal }) => loadStaffDetail(id, session.token, signal),
    enabled: valid && session.user.role === 'STAFF', retry: false, gcTime: 0,
    refetchOnWindowFocus: 'always',
    refetchInterval: (state) => state.state.data?.application.status === 'needs_info' ? 5000 : false,
  })
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }) }, [id])
  const unavailable = !valid || query.error instanceof StaffDetailError && [403, 404].includes(query.error.status)
  const detail = query.data
  const app = detail?.application
  const originalDetails = detail ? originalRequestDetails(detail) : null
  const request = detail?.initial_request ?? app
  const requestDetails = originalDetails ?? (app ? {
    purpose_details: app.purpose_details, investment_object: app.investment_object,
    collateral_type: app.collateral_type, collateral_value: app.collateral_value,
  } : {})
  function confirmed(updated: StaffDetail['application']) {
    queryClient.setQueryData<StaffDetail>(['staff-application', session.user.id, id], (current) =>
      current ? { ...current, application: updated } : current)
    void queryClient.invalidateQueries({ queryKey: ['staff-application', session.user.id, id], exact: true })
    void queryClient.invalidateQueries({ queryKey: ['staff-applications', session.user.id], exact: true })
  }
  return <div className="staff-list-page staff-application-page">
    <StaffHeader session={session} onLogout={onLogout} applicationsHref={applicationsBack} auditHref={auditBack} />
    <main className="staff-main staff-detail-main">
      <Link to={back} className="staff-detail-back">{auditBack ? '← К журналу действий' : '← К списку заявок'}</Link>
      {unavailable ? <div className="staff-detail-card staff-state" role="alert"><h1>Заявка недоступна</h1><p>Заявка не найдена или недоступна для просмотра.</p></div>
        : query.isPending ? <div className="staff-detail-card staff-state" role="status"><span className="staff-loading" />Загружаем заявку…</div>
          : query.isError ? <div className="staff-detail-card staff-state" role="alert"><h1>Не удалось загрузить заявку</h1><p>{query.error.message}</p>
            <ButtonDesktop type="button" view="secondary" size={40} loading={query.isFetching} onClick={() => void query.refetch()}>Повторить</ButtonDesktop></div>
            : detail && app && request ? <>
              <div className="staff-detail-heading">
                <div><h1>{text(detail.company.name)}</h1><p>ИНН {text(detail.company.inn)}</p>
                  <p><span>{app.number}</span><span>Создана {dateLabel(app.created_at)}</span><span>Обновлена {dateLabel(app.updated_at)}</span></p>
                </div>
                <span className="staff-status-badge">{statusLabel(app.status)}</span>
              </div>
              <div className="staff-detail-columns">
                <div className="staff-detail-primary">
                  <section className="staff-detail-card">
                    <h2>Запрос клиента</h2>
                    <div className="staff-detail-request-highlights">
                      <div><span>Продукт</span><strong>{productName(request.product_code)}</strong></div>
                      <div><span>Сумма</span><strong>{rubles(request.amount)}</strong></div>
                      <div><span>Срок</span><strong>{request.term_months == null ? 'Нет данных' : `${request.term_months} мес.`}</strong></div>
                    </div>
                    <Pairs items={[
                      ['Цель', text(request.purpose)],
                      ...Object.entries(requestDetails).map(([field, value]): Pair => [infoFieldNames[field], infoValue(field, value)]),
                    ]} />
                    {!originalDetails && <p className="staff-detail-muted">Актуальные сведения</p>}
                  </section>
                  <ClientAdditions detail={detail} />
                  <Checks checks={detail.checks} />
                  <CreditRisk detail={detail} />
                  <CompanyData detail={detail} />
                  <ApplicationHistory detail={detail} session={session} />
                </div>
                {app.status === 'manual_review' ? <StaffDecisionForm key={`${app.id}-${app.version}`} detail={detail} session={session}
                  onConfirmed={confirmed} onRefresh={async () => (await query.refetch()).isSuccess} /> : <ApplicationState detail={detail} session={session} />}
              </div>
            </> : null}
    </main>
  </div>
}
