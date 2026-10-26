import { useEffect, useState } from 'react'
import type { Session } from './auth'
import { counterFieldErrors } from './staffDecision'
import { percent, rubles } from './staffApplicationDetail'
import type { StaffDetail } from './staffApplicationDetail'
import { loadLoanPreview } from './staffPreviewApi'
import type { StaffProduct } from './staffProducts'
import type { LoanPreview } from './staffPreviewApi'

type PreviewState = { key: string; status: 'loading' } | { key: string; status: 'success'; value: LoanPreview } | { key: string; status: 'error'; message: string }

export default function StaffLoanPreview({ detail, session, action, product, amount, term, limits }: {
  detail: StaffDetail; session: Session; action: 'approve' | 'counter'; product: string; amount: string; term: string; limits: StaffProduct | undefined
}) {
  const [state, setState] = useState<PreviewState | null>(null)
  const [attempt, setAttempt] = useState(0)
  const errors = counterFieldErrors(limits, amount, term)
  const valid = !errors.amount && !errors.term && session.user.role === 'STAFF'
  const applicationId = detail.application.id
  const applicationVersion = detail.application.version
  const snapshotVersion = detail.snapshot_version
  const token = session.token
  const key = JSON.stringify([session.user.id, applicationId, applicationVersion, snapshotVersion, limits, action, product, amount, term, attempt])
  useEffect(() => {
    if (!valid) return
    const controller = new AbortController()
    let active = true
    const timer = window.setTimeout(() => {
      setState({ key, status: 'loading' })
      void loadLoanPreview({ application_id: applicationId, product_code: product, amount: Number(amount), term_months: Number(term) }, token, controller.signal, snapshotVersion)
        .then((value) => { if (active) setState({ key, status: 'success', value }) })
        .catch((cause: unknown) => {
          if (active && !controller.signal.aborted) setState({ key, status: 'error', message: cause instanceof Error ? cause.message : 'Не удалось рассчитать условия. Повторите запрос.' })
        })
    }, 500)
    return () => { active = false; window.clearTimeout(timer); controller.abort() }
  }, [valid, key, applicationId, product, amount, term, token, snapshotVersion])
  // Key matching hides an old result synchronously, including the debounce interval.
  const current = valid && state?.key === key ? state : null
  const value = current?.status === 'success' ? current.value : null
  const loading = valid && (!current || current.status === 'loading')
  return <div className="staff-loan-preview" aria-busy={loading}>
    <div className="staff-decision-calculation">
      <div><span>Ставка</span><strong>{value ? `${percent(value.annual_rate)} годовых` : '—'}</strong></div>
      <div><span>Платёж</span><strong>{value ? rubles(value.monthly_payment) : '—'}</strong></div>
      <div className="staff-preview-dscr"><span>DSCR</span><strong>{value ? new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value.dscr)) : '—'}</strong></div>
    </div>
    {!valid ? <p className="staff-detail-muted">Для расчёта укажите корректные продукт, сумму и срок.</p>
      : loading ? <p className="staff-detail-muted" role="status">Рассчитываем предварительные условия…</p>
        : current?.status === 'error' ? <div className="staff-preview-error"><p className="staff-decision-error" role="alert">{current.message}</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>Повторить расчёт</button></div>
          : value && <p className="staff-detail-muted">Предварительный расчёт</p>}
  </div>
}
