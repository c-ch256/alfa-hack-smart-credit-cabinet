import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import type { Session } from './auth'
import { staffGet } from './staffApplications'
import './ClientHeader.css'
import './StaffNotifications.css'

type Notice = {
  id: number
  application_id: number
  application_number: string
  company_name: string
  kind: 'assigned' | 'additional_info' | 'offer_accepted' | 'offer_declined' | 'withdrawn'
  created_at: string
}
const titles: Record<Notice['kind'], string> = {
  assigned: 'Вам назначена заявка',
  additional_info: 'Клиент дополнил сведения',
  offer_accepted: 'Клиент принял другие условия',
  offer_declined: 'Клиент отклонил другие условия',
  withdrawn: 'Клиент отозвал заявку',
}
async function loadNotifications(token: string, signal: AbortSignal) {
  const items = new Map<number, Notice>()
  for (let offset = 0; offset <= 1_000_000; offset += 100) {
    const page = await staffGet<Notice[]>(`/notifications?limit=100&offset=${offset}`, token, signal)
    if (!Array.isArray(page)) throw new Error('Не удалось прочитать уведомления')
    for (const item of page) items.set(item.id, item)
    if (page.length < 100) return [...items.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id)
  }
  throw new Error('Не удалось загрузить все уведомления')
}
function storedRead(key: string): number[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? '[]')
    return Array.isArray(value) ? value.filter((id): id is number => typeof id === 'number' && Number.isSafeInteger(id)) : []
  } catch { return [] }
}
function shortNumber(value: string) { return value.length > 24 ? `${value.slice(0, 13)}…${value.slice(-8)}` : value }

export default function StaffNotifications({ session, onOpen }: { session: Session; onOpen: () => void }) {
  const [open, setOpen] = useState(false)
  const storageKey = `credit-cabinet:staff-read-notices:v1:${session.user.id}`
  const [read, setRead] = useState(() => storedRead(storageKey))
  const anchor = useRef<HTMLDivElement>(null)
  const bell = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const location = useLocation()
  const query = useQuery({
    queryKey: ['staff-notifications', session.user.id],
    queryFn: ({ signal }) => loadNotifications(session.token, signal),
    staleTime: 15_000, refetchInterval: 30_000, retry: false,
  })
  const items = query.data ?? []
  const unread = items.some((item) => !read.includes(item.id))
  useEffect(() => {
    const receive = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) setRead(storedRead(storageKey))
    }
    window.addEventListener('storage', receive)
    return () => window.removeEventListener('storage', receive)
  }, [storageKey])
  useEffect(() => {
    if (!open) return
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !anchor.current?.contains(event.target)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') { setOpen(false); bell.current?.focus() }
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [open])
  function markRead(ids: number[]) {
    const merged = [...new Set([...storedRead(storageKey), ...read, ...ids])]
    setRead(merged)
    try { localStorage.setItem(storageKey, JSON.stringify(merged)) } catch { /* Preserve session state. */ }
  }
  return <div className="client-notification-anchor staff-notification-anchor" ref={anchor}>
    <button ref={bell} type="button" className="notification-button staff-notifications" aria-label={unread ? 'Уведомления: есть непрочитанные' : 'Уведомления'}
      aria-expanded={open} aria-controls={panelId} onClick={() => { setOpen(!open); if (!open) { onOpen(); void query.refetch() } }}>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4" /></svg>
      {unread && <span className="staff-unread-indicator" />}
    </button>
    {open && <section id={panelId} className="client-popover client-notifications staff-notification-panel" aria-label="Уведомления">
      <div className="client-popover-heading"><h2>Уведомления</h2><button type="button" className="client-read-all" disabled={!unread} onClick={() => markRead(items.map((item) => item.id))}>Прочитать все</button></div>
      {query.isPending ? <p className="client-panel-message" role="status">Загружаем уведомления…</p>
        : query.isError ? <div className="client-panel-message" role="alert"><p>Не удалось загрузить уведомления.</p><button type="button" className="client-read-all" onClick={() => void query.refetch()}>Повторить</button></div>
          : !items.length ? <p className="client-panel-message">Новых событий по вашим заявкам пока нет.</p>
            : <ul className="client-notices-list">{items.map((item) => <li key={item.id}>
              <Link to={`/staff/applications/${item.application_id}`} state={location.pathname === '/staff/audit' ? { staffAuditUrl: `${location.pathname}${location.search}` } : { staffListUrl: `${location.pathname}${location.search}` }}
                className="client-notice" onClick={() => { markRead([item.id]); setOpen(false) }}>
                <span className="client-notice-copy"><strong>{titles[item.kind]}</strong><span>{item.company_name}</span>
                  <span title={item.application_number}>№ {shortNumber(item.application_number)}</span>
                  <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })}</time>
                </span>
                {!read.includes(item.id) && <span className="client-notice-dot" aria-label="Не прочитано" />}
              </Link>
            </li>)}</ul>}
    </section>}
  </div>
}
