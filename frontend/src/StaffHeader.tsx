import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import type { Session } from './auth'
import StaffNotifications from './StaffNotifications'
import './Dashboard.css'
import './StaffApplicationsPage.css'

export function StaffIcon({ name }: { name: 'bell' | 'search' | 'chevron' | 'exit' }) {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'search' ? <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>
      : <path d={name === 'bell' ? 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4'
        : name === 'exit' ? 'M9 4H4v16h5M10 12h10M16 8l4 4-4 4' : 'm7 10 5 5 5-5'} />}
  </svg>
}

export default function StaffHeader({ session, onLogout, applicationsHref = '/staff/applications', auditHref = '/staff/audit' }: {
  session: Session
  onLogout: () => void
  applicationsHref?: string
  auditHref?: string
}) {
  const auditActive = useLocation().pathname === '/staff/audit'
  const [profileOpen, setProfileOpen] = useState(false)
  const profile = useRef<HTMLDivElement>(null)
  const profileButton = useRef<HTMLButtonElement>(null)
  const initials = session.user.full_name.trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join('').toLocaleUpperCase('ru-RU')
  useEffect(() => {
    if (!profileOpen) return
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !profile.current?.contains(event.target)) setProfileOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') { setProfileOpen(false); profileButton.current?.focus() }
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape) }
  }, [profileOpen])
  return (
    <header className="cabinet-header staff-header">
      <div className="cabinet-header-inner">
        <Link className="cabinet-brand staff-brand" to={applicationsHref} aria-label="Кредитный кабинет сотрудника — заявки">
          <span className="cabinet-logo" aria-hidden="true">А</span>
          <span><strong>Кредитный кабинет</strong><small>Сотрудник банка</small></span>
        </Link>
        <nav className="cabinet-nav staff-nav" aria-label="Разделы кабинета сотрудника">
          <Link className={`cabinet-nav-item${auditActive ? '' : ' is-active'}`} to={applicationsHref} aria-current={auditActive ? undefined : 'page'}>Заявки</Link>
          <Link className={`cabinet-nav-item${auditActive ? ' is-active' : ''}`} to={auditHref} aria-current={auditActive ? 'page' : undefined}>Журнал действий</Link>
        </nav>
        <div className="cabinet-profile">
          <StaffNotifications key={session.user.id} session={session} onOpen={() => setProfileOpen(false)} />
          <span className="profile-avatar" aria-hidden="true">{initials}</span>
          <div className="staff-profile" ref={profile}>
            <button ref={profileButton} type="button" className="profile-button staff-profile-trigger" aria-expanded={profileOpen}
              aria-controls="staff-profile-panel" onClick={() => setProfileOpen((open) => !open)}>
              <span className="staff-profile-name">{session.user.full_name}</span><StaffIcon name="chevron" />
            </button>
            {profileOpen && <div className="staff-profile-panel" id="staff-profile-panel">
              <button type="button" onClick={onLogout}><StaffIcon name="exit" />Выйти из кабинета</button>
            </div>}
          </div>
        </div>
      </div>
    </header>
  )
}
