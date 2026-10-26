import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import type { Session } from './auth'
import './ClientHeader.css'

export type CabinetCompany = {
  id: number
  name: string
  inn: string
  legal_form: string
  okved: string
  industry: string | null
  region: string
  registered_at: string
  bank_client_since?: string | null
  avg_monthly_revenue?: number | null
  avg_monthly_expenses?: number | null
  existing_monthly_payments?: number | null
}

type HeaderProps = {
  session: Session
  onLogout: () => void
  company?: CabinetCompany | null
  loading?: boolean
}
type ApplicationSummary = {
  id: number
  number: string
  status: string
}
type HistoryEvent = { sequence: number; to_status: string; created_at: string }
type Notice = {
  id: string
  applicationId: number
  title: string
  description: string
  createdAt: string
}
type Panel = 'notifications' | 'profile' | null
type IconName = 'bell' | 'chevron' | 'company' | 'folder' | 'exit' | 'file' | 'close'

function HeaderIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, string> = {
    bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4',
    chevron: 'M4 6 L8 10 L12 6',
    company:
      'M3 21h18M5 21V3h10v18M15 9h4v12M8 7h1M11 7h1M8 11h1M11 11h1M8 15h1M11 15h1M9 21v-3h2v3',
    folder: 'M3 7V5h6l2 2h10v13H3V7Z',
    exit: 'M9 4H4v16h5M10 12h10M16 8l4 4-4 4',
    file: 'M14 3H5v18h14V8l-5-5ZM14 3v5h5M8 12h8M8 16h5',
    close: 'm6 6 12 12M18 6 6 18',
  }
  return (
    <svg
      width="22"
      height="22"
      viewBox={name === 'chevron' ? '0 0 16 16' : '0 0 24 24'}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  )
}

async function get<T>(path: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  })
  if (!response.ok)
    throw new Error(
      response.status === 401
        ? 'Сессия завершилась. Войдите заново.'
        : 'Не удалось загрузить данные.',
    )
  return response.json() as Promise<T>
}

// Only public event types are allowed here. Never render history.reason,
// actor data, backend status_label, scoring or rejection details.
function publicNotice(event: HistoryEvent, application: ApplicationSummary): Notice | null {
  const number = application.number
  const texts: Record<string, [string, string]> = {
    approved: ['Заявка одобрена', `Ознакомьтесь с предложением по заявке № ${number}.`],
    counter_offer: [
      'Банк предложил другие условия',
      `Посмотрите предложение по заявке № ${number}.`,
    ],
    needs_info: ['Нужны дополнительные сведения', `Посмотрите запрос по заявке № ${number}.`],
    rejected: ['Решение по заявке', `Банк не одобрил заявку № ${number}.`],
    disbursed_demo: ['Кредит выдан · демо', `Оформление заявки № ${number} завершено.`],
  }
  const copy = texts[event.to_status]
  if (!copy || !Number.isFinite(Date.parse(event.created_at))) return null
  return {
    id: `${application.id}:${event.sequence}`,
    applicationId: application.id,
    title: copy[0],
    description: copy[1],
    createdAt: event.created_at,
  }
}

async function loadNotices(token: string, signal: AbortSignal): Promise<Notice[]> {
  // Bounded request volume: notifications cover the 50 most recent applications.
  const applications = await get<ApplicationSummary[]>(
    '/applications?limit=50&offset=0',
    token,
    signal,
  )
  const notices: Notice[] = []
  let nextIndex = 0
  async function worker() {
    while (nextIndex < applications.length) {
      const application = applications[nextIndex++]
      if (application.status === 'draft') continue
      for (let offset = 0; ; offset += 100) {
        const events = await get<HistoryEvent[]>(
          `/applications/${application.id}/history?limit=100&offset=${offset}`,
          token,
          signal,
        )
        for (const event of events) {
          const notice = publicNotice(event, application)
          if (notice) notices.push(notice)
        }
        if (events.length < 100) break
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, applications.length) }, () => worker()))
  return notices
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt) || b.id.localeCompare(a.id))
    .slice(0, 30)
}

function parseRead(value: string | null): string[] {
  try {
    const items: unknown = JSON.parse(value ?? '[]')
    return Array.isArray(items)
      ? items.filter((item): item is string => typeof item === 'string').slice(-5000)
      : []
  } catch {
    return []
  }
}
function readStored(key: string) {
  try {
    return parseRead(localStorage.getItem(key))
  } catch {
    return []
  }
}
function displayDate(value: string, time = false) {
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value)
  if (!Number.isFinite(date.getTime())) return '—'
  return time
    ? date.toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : date.toLocaleDateString('ru-RU')
}
function rubles(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('ru-RU', {
        style: 'currency',
        currency: 'RUB',
        maximumFractionDigits: 0,
      }).format(value)
    : '—'
}

export function CompanyDetailsDialog({
  session,
  company,
  open,
  onClose,
}: {
  session: Session
  company: CabinetCompany | null
  open: boolean
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const requisites = useQuery({
    queryKey: ['company-requisites', session.user.id, company?.id],
    enabled: open && company !== null,
    queryFn: async ({ signal }) => {
      const data = await get<{
        ogrn: string | null; ogrnip: string | null; legal_address: string;
        settlement_account: string; bank_name: string; bank_bik: string;
        correspondent_account: string | null
      }>(`/me/companies/${company!.id}/requisites`, session.token, signal)
      const registration = company!.legal_form === 'IP' ? data.ogrnip : data.ogrn
      if ([registration, data.legal_address, data.settlement_account, data.bank_name, data.bank_bik]
        .some((value) => typeof value !== 'string' || !value.trim()) ||
        (data.correspondent_account !== null && typeof data.correspondent_account !== 'string')) {
        throw new Error('Некорректные реквизиты')
      }
      return data
    },
    staleTime: 30_000,
    gcTime: 0,
    retry: false,
  })
  const bankRows: [string, string | null][] = requisites.data ? [
    company?.legal_form === 'IP' ? ['ОГРНИП', requisites.data.ogrnip] : ['ОГРН', requisites.data.ogrn],
    ['Юридический адрес', requisites.data.legal_address],
    ['Расчётный счёт', requisites.data.settlement_account],
    ['Название банка', requisites.data.bank_name],
    ['БИК', requisites.data.bank_bik],
    ...(requisites.data.correspondent_account
      ? [['Корреспондентский счёт', requisites.data.correspondent_account] as [string, string]] : []),
  ] : []
  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  }, [open])
  const rows: [string, ReactNode][] = company
    ? [
        ['Наименование', company.name],
        ['ИНН', company.inn],
        [
          'Организационная форма',
          company.legal_form === 'IP'
            ? 'Индивидуальный предприниматель'
            : company.legal_form === 'OOO'
              ? 'Общество с ограниченной ответственностью'
              : company.legal_form,
        ],
        ['ОКВЭД', [company.okved, company.industry].filter(Boolean).join(' · ')],
        ['Регион', company.region],
        ['Дата регистрации', displayDate(company.registered_at)],
        ...(company.bank_client_since
          ? [['Клиент банка с', displayDate(company.bank_client_since)] as [string, ReactNode]]
          : []),
        ...(company.avg_monthly_revenue != null
          ? [['Выручка в месяц', rubles(company.avg_monthly_revenue)] as [string, ReactNode]]
          : []),
        ...(company.avg_monthly_expenses != null
          ? [['Расходы в месяц', rubles(company.avg_monthly_expenses)] as [string, ReactNode]]
          : []),
        ...(company.existing_monthly_payments != null
          ? [
              ['Действующие кредитные платежи', rubles(company.existing_monthly_payments)] as [
                string,
                ReactNode,
              ],
            ]
          : []),
      ]
    : []
  return (
    <dialog
      ref={dialog}
      className="client-company-dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          const rect = event.currentTarget.getBoundingClientRect()
          if (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
          )
            onClose()
        }
      }}
    >
      <div className="client-company-heading">
        <div>
          <h2 id={titleId}>Данные компании</h2>
          <p>Информация из банка</p>
        </div>
        <button
          type="button"
          className="client-icon-button"
          aria-label="Закрыть данные компании"
          onClick={onClose}
          autoFocus
        >
          <HeaderIcon name="close" />
        </button>
      </div>
      {company ? (
        <dl className="client-company-details">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value || '—'}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="client-company-empty">Данные компании пока недоступны.</p>
      )}
      {company && <section className="client-company-requisites">
        <h3>Банковские реквизиты</h3>
        <p className="client-requisites-notice">Демонстрационные реквизиты.</p>
        {requisites.isError ? <div role="alert" className="client-requisites-error">
          <p>Не удалось загрузить реквизиты компании.</p>
          <button type="button" disabled={requisites.isFetching}
            onClick={() => void requisites.refetch()}>Повторить</button>
        </div> : requisites.isPending ? <p role="status">Загружаем реквизиты…</p> : (
          <dl className="client-company-details">
            {bankRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
          </dl>
        )}
      </section>}
      <div className="client-company-footer">
        <button type="button" onClick={onClose}>
          Закрыть
        </button>
      </div>
    </dialog>
  )
}

function HeaderContent({
  session,
  onLogout,
  company: suppliedCompany,
  loading = false,
}: HeaderProps) {
  const location = useLocation()
  const client = useQueryClient()
  const scope = useRef<HTMLDivElement>(null)
  const bell = useRef<HTMLButtonElement>(null)
  const profile = useRef<HTMLButtonElement>(null)
  const notificationsId = useId()
  const profileId = useId()
  const [panel, setPanel] = useState<Panel>(null)
  const [companyOpen, setCompanyOpen] = useState(false)
  const storageKey = `credit-cabinet:read-notices:v1:${session.user.id}`
  const [readIds, setReadIds] = useState<string[]>(() => readStored(storageKey))
  const companies = useQuery({
    queryKey: ['client-header-companies', session.user.id],
    queryFn: ({ signal }) => get<CabinetCompany[]>('/me/companies', session.token, signal),
    enabled: suppliedCompany === undefined,
    staleTime: 30_000,
    retry: false,
  })
  const company = suppliedCompany === undefined ? (companies.data?.[0] ?? null) : suppliedCompany
  const companyName =
    company?.name ??
    (loading || (suppliedCompany === undefined && companies.isPending)
      ? 'Загрузка…'
      : session.user.full_name)
  const initials = companyName
    .replace(/[«»"]/g, '')
    .split(/\s+/)
    .filter((word) => !['ИП', 'ООО'].includes(word))
    .slice(0, 2)
    .map((word) => word[0])
    .join('')
    .toUpperCase()
  const notices = useQuery({
    queryKey: ['client-notifications', session.user.id],
    queryFn: ({ signal }) => loadNotices(session.token, signal),
    staleTime: 15_000,
    refetchInterval: panel === 'notifications' ? 30_000 : false,
    retry: false,
  })
  const items = notices.data ?? []
  const unread = items.some((item) => !readIds.includes(item.id))

  useEffect(() => {
    function receive(event: StorageEvent) {
      if (event.key === storageKey || event.key === null) setReadIds(parseRead(event.newValue))
    }
    window.addEventListener('storage', receive)
    return () => window.removeEventListener('storage', receive)
  }, [storageKey])
  useEffect(() => {
    if (!panel) return
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !scope.current?.contains(event.target)) setPanel(null)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setPanel(null)
        ;(panel === 'notifications' ? bell : profile).current?.focus()
      }
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [panel])

  function markRead(ids: string[]) {
    const merged = [...new Set([...readStored(storageKey), ...readIds, ...ids])].slice(-5000)
    setReadIds(merged)
    try {
      localStorage.setItem(storageKey, JSON.stringify(merged))
    } catch {
      /* Keep the read state in this session if storage is unavailable. */
    }
  }
  function showCompany() {
    setPanel(null)
    setCompanyOpen(true)
  }
  const active =
    location.pathname === '/'
      ? 'home'
      : location.pathname.startsWith('/credits')
        ? 'credits'
        : 'applications'
  return (
    <>
      <header className="cabinet-header client-header">
        <div className="cabinet-header-inner">
          <Link
            to="/"
            className="cabinet-brand client-brand"
            aria-label="Кредитный кабинет — главная"
          >
            <span className="cabinet-logo" aria-hidden="true">
              А
            </span>
            <span>Кредитный кабинет</span>
          </Link>
          <nav className="cabinet-nav" aria-label="Главное меню">
            {(
              [
                ['home', '/', 'Главная'],
                ['credits', '/credits', 'Кредиты'],
                ['applications', '/applications', 'Мои заявки'],
              ] as const
            ).map(([name, path, label]) => (
              <Link
                key={name}
                to={path}
                className={`cabinet-nav-item${active === name ? ' is-active' : ''}`}
                aria-current={active === name ? 'page' : undefined}
              >
                {label}
              </Link>
            ))}
          </nav>
          <div
            ref={scope}
            className="cabinet-profile client-header-controls"
            onBlur={(event) => {
              if (
                event.relatedTarget instanceof Node &&
                !event.currentTarget.contains(event.relatedTarget)
              )
                setPanel(null)
            }}
          >
            <div className="client-notification-anchor">
              <button
                ref={bell}
                type="button"
                className="notification-button client-bell"
                aria-label={unread ? 'Уведомления: есть непрочитанные' : 'Уведомления'}
                aria-expanded={panel === 'notifications'}
                aria-controls={notificationsId}
                onClick={() => {
                  setPanel(panel === 'notifications' ? null : 'notifications')
                  if (panel !== 'notifications') void notices.refetch()
                }}
              >
                <HeaderIcon name="bell" />
                {unread && <span className="client-unread-indicator" />}
              </button>
              {panel === 'notifications' && (
                <section
                  id={notificationsId}
                  className="client-popover client-notifications"
                  aria-label="Уведомления"
                >
                  <div className="client-popover-heading">
                    <h2>Уведомления</h2>
                    <button
                      type="button"
                      className="client-read-all"
                      disabled={!unread}
                      onClick={() => markRead(items.map((item) => item.id))}
                    >
                      Прочитать все
                    </button>
                  </div>
                  {notices.isPending ? (
                    <p className="client-panel-message" role="status">
                      Загружаем уведомления…
                    </p>
                  ) : notices.isError ? (
                    <div className="client-panel-message" role="alert">
                      <p>Не удалось обновить уведомления.</p>
                      <button
                        type="button"
                        className="client-read-all"
                        onClick={() => void notices.refetch()}
                      >
                        Повторить
                      </button>
                    </div>
                  ) : items.length === 0 ? (
                    <p className="client-panel-message">Новых событий по заявкам пока нет.</p>
                  ) : (
                    <ul className="client-notices-list">
                      {items.map((item) => (
                        <li key={item.id}>
                          <Link
                            to={`/applications/${item.applicationId}`}
                            className="client-notice"
                            onClick={() => {
                              markRead([item.id])
                              setPanel(null)
                            }}
                          >
                            <span className="client-notice-icon">
                              <HeaderIcon name="file" />
                            </span>
                            <span className="client-notice-copy">
                              <strong>{item.title}</strong>
                              <span>{item.description}</span>
                              <time dateTime={item.createdAt}>
                                {displayDate(item.createdAt, true)}
                              </time>
                            </span>
                            {!readIds.includes(item.id) && (
                              <span className="client-notice-dot" aria-label="Не прочитано" />
                            )}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                  <Link
                    to="/applications"
                    className="client-popover-footer"
                    onClick={() => setPanel(null)}
                  >
                    Перейти к заявкам <span aria-hidden="true">→</span>
                  </Link>
                </section>
              )}
            </div>
            <span className="profile-avatar" aria-hidden="true">
              {initials || '…'}
            </span>
            <div className="client-profile-anchor">
              <button
                ref={profile}
                type="button"
                className="profile-button client-profile-trigger"
                aria-expanded={panel === 'profile'}
                aria-controls={profileId}
                onClick={() => setPanel(panel === 'profile' ? null : 'profile')}
              >
                <span className="client-profile-name">{companyName}</span>
                <HeaderIcon name="chevron" />
              </button>
              {panel === 'profile' && (
                <div id={profileId} className="client-popover client-profile-panel">
                  <div className="client-profile-identity">
                    <strong>{companyName}</strong>
                    {company && <span>ИНН {company.inn}</span>}
                  </div>
                  <nav aria-label="Действия профиля">
                    <button type="button" className="client-profile-action" onClick={showCompany}>
                      <HeaderIcon name="company" />
                      Данные компании
                    </button>
                    <Link
                      to="/applications"
                      className="client-profile-action"
                      onClick={() => setPanel(null)}
                    >
                      <HeaderIcon name="folder" />
                      Мои заявки
                    </Link>
                  </nav>
                  <button
                    type="button"
                    className="client-profile-action client-logout"
                    onClick={() => {
                      setPanel(null)
                      client.clear()
                      onLogout()
                    }}
                  >
                    <HeaderIcon name="exit" />
                    Выйти из кабинета
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>
      <CompanyDetailsDialog
        session={session}
        company={company}
        open={companyOpen}
        onClose={() => setCompanyOpen(false)}
      />
    </>
  )
}

export default function ClientHeader(props: HeaderProps) {
  return <HeaderContent key={props.session.user.id} {...props} />
}
