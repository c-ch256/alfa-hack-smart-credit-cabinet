import { lazy, Suspense, useEffect, useState } from 'react'
import LoginPage from './LoginPage'
import Dashboard from './Dashboard'
import type { Session } from './auth'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import CreditCalculator from './CreditCalculator'
import MyApplications from './MyApplications'
import DraftApplicationPage from './DraftApplicationPage'
import ApplicationPage from './ApplicationPage'

const StaffApplicationsPage = lazy(() => import('./StaffApplicationsPage'))
const StaffApplicationPage = lazy(() => import('./StaffApplicationPage'))
const StaffAuditPage = lazy(() => import('./StaffAuditPage'))

function App() {
  const navigate = useNavigate()
  const [session, setSession] = useState<Session | null>(null)

  useEffect(() => {
    if (!session) return

    const timer = window.setTimeout(() => {
      setSession(null)
    }, Math.max(0, session.expiresAt - Date.now()))

    return () => window.clearTimeout(timer)
  }, [session])

  if (!session) {
    return <LoginPage
  onLogin={(nextSession) => {
    setSession(nextSession)
    navigate(nextSession.user.role === 'STAFF' ? '/staff/applications' : '/', { replace: true })
  }}
/>
  }

  if (session.user.role === 'STAFF') {
    return (
      <Routes>
        <Route path="/staff/audit" element={
          <Suspense fallback={<main role="status" style={{ padding: 40 }}>Загружаем журнал действий…</main>}>
            <StaffAuditPage session={session} onLogout={() => setSession(null)} />
          </Suspense>
        } />
        <Route path="/staff/applications" element={
          <Suspense fallback={<main role="status" style={{ padding: 40 }}>Загружаем кабинет сотрудника…</main>}>
            <StaffApplicationsPage session={session} onLogout={() => setSession(null)} />
          </Suspense>
        } />
        <Route path="/staff/applications/:id" element={
          <Suspense fallback={<main role="status" style={{ padding: 40 }}>Загружаем заявку…</main>}>
            <StaffApplicationPage session={session} onLogout={() => setSession(null)} />
          </Suspense>
        } />
        <Route path="*" element={<Navigate to="/staff/applications" replace />} />
      </Routes>
    )
  }

  return (
  <Routes>
    <Route
      path="/"
      element={
        <Dashboard
          session={session}
          onLogout={() => setSession(null)}
        />
      }
    />

    <Route
      path="/credits"
      element={
        <CreditCalculator
          session={session}
          onLogout={() => setSession(null)}
        />
      }
    />
    <Route
  path="/applications"
  element={
    <MyApplications
      session={session}
      onLogout={() => setSession(null)}
    />
  }
/>
<Route
  path="/applications/:id/edit"
  element={
    <DraftApplicationPage
      session={session}
      onLogout={() => setSession(null)}
    />
  }
/>
<Route
  path="/applications/:id"
  element={
    <ApplicationPage
      session={session}
      onLogout={() => setSession(null)}
    />
  }
/>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
)
}

export default App
