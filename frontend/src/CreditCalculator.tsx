import { indicativeRate, indicativePayment, indicativeRateLabel } from './indicativeCalculation'
import { useEffect, useId, useRef, useState } from 'react'
import type { CSSProperties, FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import type { Session } from './auth'
import ClientHeader from './ClientHeader'
import './Dashboard.css'
import './CreditCalculator.css'
import './DraftCancel.css'

type Product = {
  code: string
  indicative_rate_min?: number | string | null
  name: string
  min_amount: number
  max_amount: number
  min_term_months: number
  max_term_months: number
}

type Company = {
  id: number; name: string; inn: string; legal_form: string
  okved: string; industry: string | null; region: string
  registered_at: string; bank_client_since: string
  avg_monthly_revenue: number; avg_monthly_expenses: number
  existing_monthly_payments: number
}
type Application = { id: number; number: string; status: string }
export type DraftApplication = {
  id: number
  number: string
  status: string
  company_id: number
  product_code: string
  amount: number
  term_months: number
  purpose: string
  purpose_details: string | null
  investment_object: string | null
  collateral_type: string | null
  collateral_value: number | null
}

type Props = {
  session: Session
  onLogout: () => void
  initialDraft?: DraftApplication
}
type Goal = { id: string; label: string; code: string; description: string; icon: string }
type Selection = { goal: Goal; product: Product; amount: number; months: number }

const goals: Goal[] = [
  { id: 'expenses', label: 'Текущие расходы', code: 'working_capital', icon: 'box',
    description: 'Для оплаты поставщикам, аренды, зарплаты и других текущих расходов бизнеса.' },
  { id: 'stock', label: 'Закупка товаров', code: 'working_capital', icon: 'cart',
    description: 'Для закупки товаров, сырья и материалов, пополнения запасов.' },
  { id: 'cash-gap', label: 'Кассовый разрыв', code: 'working_capital', icon: 'clock',
    description: 'Чтобы оплатить текущие расходы, пока вы ждёте поступления от покупателей.' },
  { id: 'equipment', label: 'Оборудование', code: 'investment', icon: 'chart',
    description: 'Для покупки и модернизации оборудования, расширения производства.' },
  { id: 'property', label: 'Недвижимость', code: 'investment', icon: 'building',
    description: 'Для покупки помещения, склада или другого объекта для бизнеса.' },
  { id: 'transport', label: 'Транспорт', code: 'investment', icon: 'truck',
    description: 'Для приобретения транспорта и спецтехники для вашего бизнеса.' },
]

const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 })
const money = (value: number) => `${number.format(value)} ₽`
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

function plural(value: number, forms: [string, string, string]) {
  const lastTwo = value % 100
  if (lastTwo >= 11 && lastTwo <= 14) return forms[2]
  const last = value % 10
  return last === 1 ? forms[0] : last >= 2 && last <= 4 ? forms[1] : forms[2]
}

function formatTerm(months: number, afterDo = false) {
  const years = Math.floor(months / 12)
  const rest = months % 12
  const parts: string[] = []
  if (years) parts.push(`${years} ${plural(years, afterDo ? ['года', 'лет', 'лет'] : ['год', 'года', 'лет'])}`)
  if (rest || !years) parts.push(`${rest} ${plural(rest, afterDo ? ['месяца', 'месяцев', 'месяцев'] : ['месяц', 'месяца', 'месяцев'])}`)
  return parts.join(' ')
}

function amountStops(min: number, max: number) {
  const stops = new Set<number>([min, max])
  const bands = [
    { start: 0, end: 1_000_000, step: 1_000 },
    { start: 1_000_000, end: 15_000_000, step: 50_000 },
    { start: 15_000_000, end: max, step: 1_000_000 },
  ]
  for (const band of bands) {
    const start = band.start + Math.ceil((Math.max(min, band.start) - band.start) / band.step) * band.step
    for (let value = start; value <= Math.min(max, band.end); value += band.step) stops.add(value)
  }
  return [...stops].sort((a, b) => a - b)
}

function nearestStop(stops: number[], value: number) {
  let low = 0
  let high = stops.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (stops[middle] < value) low = middle + 1
    else high = middle
  }
  return low > 0 && value - stops[low - 1] < stops[low] - value ? stops[low - 1] : stops[low]
}

function CheckIcon() {
  return <svg className="credit-check-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m7 12 3.2 3.2L17 8.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

function ChevronIcon() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 7.5 5 5 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

function MoneyInput({ value, onChange, required = false, placeholder }: {
  value: string; onChange: (value: string) => void; required?: boolean; placeholder?: string
}) {
  return <input type="text" inputMode="numeric" autoComplete="off" required={required}
    maxLength={24} placeholder={placeholder} value={value === '' ? '' : money(Number(value))}
    onFocus={(event) => {
      const input = event.currentTarget
      const end = input.value.indexOf(' ₽')
      if (end >= 0) input.setSelectionRange(end, end)
    }}
    onChange={(event) => {
      const input = event.currentTarget
      const digitsBeforeCaret = input.value.slice(0, input.selectionStart ?? input.value.length).replace(/\D/g, '').length
      const digits = input.value.replace(/\D/g, '')
      onChange(digits)
      requestAnimationFrame(() => {
        if (document.activeElement !== input) return
        let position = 0
        let count = 0
        while (position < input.value.length && count < digitsBeforeCaret) {
          if (/\d/.test(input.value[position])) count++
          position++
        }
        input.setSelectionRange(position, position)
      })
    }} />
}

async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  if (init.body) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(`/api${path}`, { ...init, headers })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new Error('Нет соединения с сервером. Попробуйте ещё раз.', { cause })
  }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Сессия завершена. Выйдите и войдите снова.')
    if (response.status === 422) throw new Error('Сервер не принял данные. Проверьте заполнение анкеты.')
    if (response.status === 409) throw new Error('Данные заявки изменились. Обновите страницу и проверьте её статус.')
    throw new Error('Не удалось выполнить запрос. Попробуйте ещё раз.')
  }
  return response.json() as Promise<T>
}

function GoalIcon({ kind }: { kind: string }) {
  const paths: Record<string, string> = {
    box: 'M12 3 3 8l9 5 9-5-9-5ZM3 8v9l9 5 9-5V8M12 13v9M7.5 5.5l9 5',
    cart: 'M3 4h2l2 12h12l2-8H6M9 21h.01M18 21h.01',
    clock: 'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
    chart: 'M4 21V12h4v9M10 21V7h4v14M16 21V3h4v18',
    building: 'M3 21h18M5 21V3h14v18M9 7h1M14 7h1M9 11h1M14 11h1M10 21v-6h4v6',
    truck: 'M3 6h11v12H3V6ZM14 10h4l3 4v4h-7M8 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM20 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z',
  }
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[kind]} /></svg>
}

function RangeCard({ id, label, value, min, max, suffix, onChange }: {
  id: string; label: string; value: string; min: number; max: number
  suffix: string; onChange: (value: string) => void
}) {
  const [editingTerm, setEditingTerm] = useState(false)
  const isAmount = suffix === '₽'
  const stops = isAmount ? amountStops(min, max) : []
  const numeric = Number(value)
  const valid = value !== '' && Number.isSafeInteger(numeric) && numeric >= min && numeric <= max
  const progress = max === min ? 100 : (clamp(numeric, min, max) - min) / (max - min) * 100
  const display = value === '' ? '' : number.format(numeric)
  return (
    <section className="credit-range-card">
      <label htmlFor={id}>{label}</label>
      <div className="credit-value-line">
        {!isAmount && !editingTerm ? (
          <button id={id} className="credit-term-value" type="button" title="Нажмите, чтобы ввести срок в месяцах" onClick={() => setEditingTerm(true)}>{valid ? formatTerm(numeric) : 'Укажите срок'}</button>
        ) : <>
        <span className="credit-value-editor">
        <span className="credit-value-measure" aria-hidden="true">{display || '0'}</span>
        <input id={id} type="text" inputMode="numeric" autoComplete="off" maxLength={18}
          autoFocus={!isAmount} value={display}
          onChange={(event) => onChange(event.target.value.replace(/\D/g, ''))}
          onBlur={() => { onChange(String(clamp(Number(value) || min, min, max))); setEditingTerm(false) }}
          aria-invalid={!valid} aria-describedby={`${id}-limits`} />
        </span>
        <span>{suffix}</span>
        </>}
      </div>
      <input className="credit-range" type="range" min={min} max={max} step={1}
        value={clamp(numeric || min, min, max)} aria-label={`${label}: ползунок`}
        aria-valuetext={isAmount ? money(numeric) : formatTerm(numeric)}
        style={{ '--range-progress': `${progress}%` } as CSSProperties}
        onChange={(event) => onChange(String(isAmount ? nearestStop(stops, Number(event.target.value)) : Number(event.target.value)))}
        onKeyDown={(event) => {
          if (!isAmount) return
          const forward = event.key === 'ArrowRight' || event.key === 'ArrowUp' || event.key === 'PageUp'
          const backward = event.key === 'ArrowLeft' || event.key === 'ArrowDown' || event.key === 'PageDown'
          if (!forward && !backward) return
          event.preventDefault()
          let next = numeric
          const count = event.key.startsWith('Page') ? 10 : 1
          for (let index = 0; index < count; index++) {
            next = forward ? stops.find((stop) => stop > next) ?? max : [...stops].reverse().find((stop) => stop < next) ?? min
          }
          onChange(String(next))
        }} />
      <div className="credit-range-limits" id={`${id}-limits`}>
        <span>{isAmount ? money(min) : formatTerm(min)}</span><span>{isAmount ? money(max) : formatTerm(max)}</span>
      </div>
      {!valid && <p className="credit-field-error">Укажите значение от {number.format(min)} до {number.format(max)}.</p>}
    </section>
  )
}

function ApplicationForm({
  selection,
  companies,
  session,
  initialDraft,
  onBack,
  onDone,
}: {
  selection: Selection
  companies: Company[]
  session: Session
  initialDraft?: DraftApplication
  onBack: () => void
  onDone: (application: Application) => void
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const cancelDialog = useRef<HTMLDialogElement>(null)
  const cancelTitleId = useId()
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const { product } = selection
  const [goalId, setGoalId] = useState(selection.goal.id)
  const [amountText, setAmountText] = useState(String(selection.amount))
  const [monthsText, setMonthsText] = useState(String(selection.months))
  const [companyId, setCompanyId] = useState(
  String(initialDraft?.company_id ?? companies[0]?.id ?? ''),
)
const [details, setDetails] = useState(
  initialDraft?.purpose_details ?? '',
)
const [object, setObject] = useState(
  initialDraft?.investment_object ?? '',
)
const [collateral, setCollateral] = useState(
  initialDraft?.collateral_type ?? '',
)
const [collateralValue, setCollateralValue] = useState(
  initialDraft?.collateral_value == null
    ? ''
    : String(initialDraft.collateral_value),
)
  const [action, setAction] = useState<'save' | 'submit' | null>(null)
  const [locked, setLocked] = useState(false)
  const [retryAction, setRetryAction] = useState<'save' | 'submit' | null>(null)
  const [hasSavedDraft, setHasSavedDraft] = useState(initialDraft !== undefined)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')
  const [companyExpanded, setCompanyExpanded] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const busyRef = useRef(false)
  const draftId = useRef<number | undefined>(initialDraft?.id)
  const request = useRef<{
    key: string; body: string; id?: number; needsPatch: boolean; action: 'save' | 'submit'
  } | null>(null)
  const company = companies.find((item) => String(item.id) === companyId)
  const choices = goals.filter((item) => item.code === product.code)
  if (!choices.some((item) => item.id === selection.goal.id)) {
  choices.push(selection.goal)
}
  const goal = choices.find((item) => item.id === goalId) ?? selection.goal
  const amount = Number(amountText)
  const months = Number(monthsText)
  const annualRate = indicativeRate(product.indicative_rate_min)
  const monthlyPayment = indicativePayment(amount, months, annualRate)
  const valid = amountText !== '' && monthsText !== '' && Number.isSafeInteger(amount) && Number.isSafeInteger(months)
    && amount >= product.min_amount && amount <= product.max_amount
    && months >= product.min_term_months && months <= product.max_term_months
  const investment = product.code === 'investment'
  const needsCollateral = investment && amount >= 5_000_000

  useEffect(() => {
    const element = cancelDialog.current
    if (!element) return
    if (cancelOpen && !element.open) element.showModal()
    if (!cancelOpen && element.open) element.close()
  }, [cancelOpen])

  function invalidateApplications() {
    for (const queryKey of [
      ['dashboard', session.user.id],
      ['my-applications', session.user.id],
      ['draft-application', session.user.id],
      ['client-notifications', session.user.id],
    ]) void queryClient.invalidateQueries({ queryKey })
  }

  async function cancelDraft() {
    const id = draftId.current
    if (id === undefined || busyRef.current) return
    busyRef.current = true
    setCancelling(true)
    setCancelError('')
    try {
      // Never cancel an application that has left the draft stage elsewhere.
      const current = await api<Application>(`/applications/${id}`, session.token)
      if (current.status !== 'draft') {
        setCancelOpen(false)
        invalidateApplications()
        onDone(current)
        return
      }
      await api<Application>(`/applications/${id}/withdraw`, session.token, { method: 'POST' })
      setCancelOpen(false)
      invalidateApplications()
      navigate('/applications', { replace: true })
    } catch (cause) {
      setCancelError(cause instanceof Error ? cause.message : 'Не удалось отменить черновик. Попробуйте ещё раз.')
    } finally {
      busyRef.current = false
      setCancelling(false)
    }
  }

  function changed() {
    setSaved('')
    setError('')
  }

  async function persist(nextAction: 'save' | 'submit') {
    if (busyRef.current || !company) return
    if (!request.current) {
      if (!formRef.current?.reportValidity()) return
      if (!valid) {
        setError(`Укажите сумму от ${money(product.min_amount)} до ${money(product.max_amount)} и срок от ${product.min_term_months} до ${product.max_term_months} месяцев.`)
        return
      }
      if (nextAction === 'submit' && investment && !object.trim()) {
        setError('Укажите объект инвестирования.')
        return
      }
      const value = Number(collateralValue)
      if (nextAction === 'submit' && needsCollateral && (!collateral.trim() || !Number.isSafeInteger(value) || value <= 0)) {
        setError('Укажите вид залога и его стоимость целым числом больше нуля.')
        return
      }
      request.current = {
        key: crypto.randomUUID(), id: draftId.current,
        needsPatch: draftId.current !== undefined, action: nextAction,
        body: JSON.stringify({
          company_id: company.id, product_code: product.code, amount, term_months: months,
          purpose: goal.label, purpose_details: details.trim() || null,
          investment_object: investment ? object.trim() || null : null,
          collateral_type: needsCollateral ? collateral.trim() || null : null,
          collateral_value: needsCollateral && collateralValue !== '' ? value : null,
        }),
      }
    }
    const pending = request.current
    busyRef.current = true
    setAction(pending.action)
    setRetryAction(pending.action)
    setLocked(true)
    setError('')
    setSaved('')
    try {
      if (pending.id === undefined) {
        const draft = await api<Application>('/applications', session.token, {
          method: 'POST', headers: { 'Idempotency-Key': pending.key }, body: pending.body,
        })
        pending.id = draft.id
        draftId.current = draft.id
        setHasSavedDraft(true)
        void queryClient.invalidateQueries({ queryKey: ['dashboard', session.user.id] })
      }
      // Проверка также восстанавливает состояние после прерванного запроса.
      let application = await api<Application>(`/applications/${pending.id}`, session.token)
      if (application.status !== 'draft') {
        onDone(application)
        return
      }
      if (pending.needsPatch) {
        const { company_id: companyIdToKeep, ...patch } = JSON.parse(pending.body) as Record<string, unknown>
        // Компания существующего черновика не изменяется API.
        void companyIdToKeep
        application = await api<Application>(`/applications/${pending.id}`, session.token, {
          method: 'PATCH', body: JSON.stringify(patch),
        })
        pending.needsPatch = false
      }
      if (pending.action === 'submit') {
        application = await api<Application>(`/applications/${pending.id}/submit`, session.token, { method: 'POST' })
        void queryClient.invalidateQueries({ queryKey: ['dashboard', session.user.id] })
        onDone(application)
      } else {
        setSaved(`Черновик ${application.number} сохранён. Заявка ещё не отправлена в банк.`)
        request.current = null
        setRetryAction(null)
        setLocked(false)
        void queryClient.invalidateQueries({ queryKey: ['dashboard', session.user.id] })
      }
    } catch (cause) {
      setError(`${cause instanceof Error ? cause.message : 'Не удалось выполнить запрос.'} Повторите действие с теми же данными.`)
    } finally {
      busyRef.current = false
      setAction(null)
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void persist('submit')
  }

  const date = company ? new Date(company.registered_at) : null
  const now = new Date()
  const anniversaryPassed = date && (now.getMonth() > date.getMonth() || now.getMonth() === date.getMonth() && now.getDate() >= date.getDate())
  const years = date ? Math.max(0, now.getFullYear() - date.getFullYear() - (anniversaryPassed ? 0 : 1)) : 0
  const age = years ? new Intl.NumberFormat('ru-RU', { style: 'unit', unit: 'year', unitDisplay: 'long' }).format(years) : 'Менее года'

  return (
    <>
      <dialog
        ref={cancelDialog}
        className="loan-cancel-dialog"
        aria-labelledby={cancelTitleId}
        aria-busy={cancelling}
        onCancel={(event) => { event.preventDefault(); if (!cancelling) setCancelOpen(false) }}
        onClose={() => setCancelOpen(false)}
        onClick={(event) => {
          if (event.target !== event.currentTarget || cancelling) return
          const rect = event.currentTarget.getBoundingClientRect()
          if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) setCancelOpen(false)
        }}
      >
        <button type="button" className="loan-cancel-close" aria-label="Закрыть подтверждение удаления" disabled={cancelling} onClick={() => setCancelOpen(false)}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
        </button>
        <h2 id={cancelTitleId}>Удалить черновик?</h2>
        <p>Черновик будет закрыт и останется в истории. Продолжить его заполнение будет нельзя.</p>
        {cancelError && <p className="loan-cancel-error" role="alert">{cancelError}</p>}
        <div className="loan-cancel-actions">
          <ButtonDesktop type="button" view="secondary" size={48} className="loan-cancel-return" disabled={cancelling} onClick={() => setCancelOpen(false)} autoFocus>
            Вернуться
          </ButtonDesktop>
          <ButtonDesktop type="button" view="accent" size={48} disabled={cancelling} loading={cancelling} onClick={() => void cancelDraft()}>
            Удалить черновик
          </ButtonDesktop>
        </div>
      </dialog>

      <button className="credit-back" type="button" onClick={onBack} disabled={locked}><ChevronIcon />
{initialDraft
  ? 'Вернуться к заявкам'
  : 'Вернуться к подбору кредита'}</button>
      <div className="loan-application-layout">
        <div className="loan-application-left">
          <div className="credit-heading loan-application-heading">
            <h1>{investment ? 'Заявка на инвестиционный кредит' : 'Заявка на оборотный кредит'}</h1>
            <p>Проверьте условия и дополните необходимые сведения</p>
          </div>
          {!company ? <div className="credit-notice">К вашему профилю пока не привязана компания. Оформление заявки недоступно.</div> : (
            <form className="loan-application-card" ref={formRef} onSubmit={submit} onChange={changed}>
              <fieldset disabled={locked}>
                <h2>Условия кредита</h2>
                {companies.length > 1 && <label className="loan-field">Компания<select disabled={hasSavedDraft} value={companyId} onChange={(event) => setCompanyId(event.target.value)}>{companies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
                <label className="loan-field loan-purpose">Цель кредита<select value={goal.id} onChange={(event) => setGoalId(event.target.value)}>{choices.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
                <div className="loan-two-fields">
                  <label className="loan-field">Сумма кредита<MoneyInput required value={amountText} onChange={setAmountText} /><span className="loan-hint">От {money(product.min_amount)} до {money(product.max_amount)}</span></label>
                  <label className="loan-field">Срок кредита, мес.<input required type="number" min={product.min_term_months} max={product.max_term_months} step={1} value={monthsText} onChange={(event) => setMonthsText(event.target.value)} /><span className="loan-hint">От {product.min_term_months} до {product.max_term_months} месяцев</span></label>
                </div>
                {investment && <label className="loan-field">Объект инвестирования<input maxLength={255} value={object} onChange={(event) => setObject(event.target.value)} placeholder="Например, производственная линия" /></label>}
                {needsCollateral && <div className="loan-two-fields"><label className="loan-field">Вид залога<input maxLength={128} value={collateral} onChange={(event) => setCollateral(event.target.value)} placeholder="Например, оборудование" /></label><label className="loan-field">Стоимость залога<MoneyInput value={collateralValue} onChange={setCollateralValue} placeholder="Укажите стоимость, ₽" /></label></div>}
                <label className="loan-field loan-comment">Комментарий к заявке (необязательно)<textarea maxLength={500} rows={3} value={details} aria-describedby="loan-comment-count" onChange={(event) => setDetails(event.target.value.slice(0, 500))} placeholder="Например, закупка товаров к сезону" /><span className="loan-comment-count" id="loan-comment-count">{details.length} / 500</span></label>
              </fieldset>
              {error && <p className="credit-field-error" role="alert">{error}</p>}
              {saved && <p className="loan-saved" role="status">{saved}</p>}
              {locked && error && <p className="loan-hint">Поля временно зафиксированы, чтобы безопасно повторить действие. Сохранённый черновик останется в кабинете.</p>}
              <div className="loan-application-buttons">
                <ButtonDesktop type="submit" view="accent" size={48} block loading={action === 'submit'} disabled={action !== null || locked && retryAction !== 'submit'}>{locked && error && retryAction === 'submit' ? 'Повторить отправку' : 'Отправить заявку'}</ButtonDesktop>
                <ButtonDesktop type="button" view="secondary" size={48} block loading={action === 'save'} disabled={action !== null || locked && retryAction !== 'save'} onClick={() => void persist('save')}>{locked && error && retryAction === 'save' ? 'Повторить сохранение' : 'Сохранить черновик'}</ButtonDesktop>
              </div>
              {hasSavedDraft && (
                <div className="loan-draft-cancel-row">
                  <button
                    type="button"
                    className="loan-draft-cancel-link"
                    disabled={action !== null || locked || cancelling}
                    onClick={() => { setCancelError(''); setCancelOpen(true) }}
                  >
                    Удалить черновик
                  </button>
                </div>
              )}
              <p className="loan-form-footnote">После отправки заявка поступит на рассмотрение банка. Вы сможете отслеживать её статус в разделе «Мои заявки».</p>
            </form>
          )}
        </div>
        <aside className="loan-application-right" aria-label="Расчёт и данные компании">
          <section className="loan-side-card loan-calculation">
            <h2>Ваш расчёт</h2>
            <span className="loan-payment-label">Ежемесячный платёж</span>
            <strong className="loan-payment" aria-live="polite" aria-atomic="true">{valid && monthlyPayment !== null ? `от ${money(monthlyPayment)}` : '—'}</strong>
            <dl className="loan-data-list">
              <div><dt>Сумма кредита</dt><dd>{valid ? money(amount) : '—'}</dd></div>
              <div><dt>Срок</dt><dd>{valid ? formatTerm(months) : '—'}</dd></div>
              <div><dt>Демо-ставка</dt><dd>{indicativeRateLabel(annualRate)}</dd></div>
            </dl>
            <p className="loan-form-footnote">{annualRate === null ? 'Предварительный расчёт недоступен: сервер не передал ставку.' : 'Предварительный расчёт. Не является предложением банка.'}</p>
          </section>
          {company && <section className="loan-side-card loan-company">
            <div className="loan-company-heading"><h2>Данные компании</h2><span><CheckIcon />Из банка</span></div>
            <p className="loan-company-name">{company.name}</p>
            <dl className="loan-data-list">
              <div><dt>ИНН</dt><dd>{company.inn}</dd></div>
              <div><dt>ОКВЭД</dt><dd>{company.okved}{company.industry ? ` · ${company.industry}` : ''}</dd></div>
              <div><dt>Регион</dt><dd>{company.region}</dd></div>
              <div><dt>Срок ведения бизнеса</dt><dd>{age}</dd></div>
            </dl>
            <dl className="loan-data-list loan-finances">
              <div><dt>Выручка в месяц</dt><dd>{money(company.avg_monthly_revenue)}</dd></div>
              <div><dt>Расходы в месяц</dt><dd>{money(company.avg_monthly_expenses)}</dd></div>
              <div><dt>Действующие кредитные платежи</dt><dd>{money(company.existing_monthly_payments)}</dd></div>
            </dl>
            {companyExpanded && <div className="loan-company-extra" id="loan-company-extra"><dl className="loan-data-list"><div><dt>Форма организации</dt><dd>{company.legal_form === 'IP' ? 'ИП' : company.legal_form === 'OOO' ? 'ООО' : company.legal_form}</dd></div><div><dt>Дата регистрации</dt><dd>{new Date(company.registered_at).toLocaleDateString('ru-RU')}</dd></div><div><dt>Клиент банка с</dt><dd>{new Date(company.bank_client_since).toLocaleDateString('ru-RU')}</dd></div></dl></div>}
            <button type="button" className="loan-company-toggle" aria-expanded={companyExpanded} aria-controls="loan-company-extra" onClick={() => setCompanyExpanded((expanded) => !expanded)}>{companyExpanded ? 'Скрыть дополнительные данные' : 'Показать все данные'}<ChevronIcon /></button>
          </section>}
        </aside>
      </div>
      <div className="credit-notice"><span aria-hidden="true">ⓘ</span><p>При необходимости банк может запросить дополнительные сведения после рассмотрения заявки.</p></div>
    </>
  )
}

function CalculatorBody({
  products,
  companies,
  session,
  initialDraft,
}: {
  products: Product[]
  companies: Company[]
  session: Session
  initialDraft?: DraftApplication
}) {
  const navigate = useNavigate()
  const available = goals.filter((goal) => products.some((product) => product.code === goal.code))
  const initial = products.find((product) => product.code === available[0]?.code)
  const [goalId, setGoalId] = useState(available[0]?.id ?? '')
  const [amountText, setAmountText] = useState(String(initial ? clamp(5_000_000, initial.min_amount, initial.max_amount) : 0))
  const [monthsText, setMonthsText] = useState(String(initial ? clamp(24, initial.min_term_months, initial.max_term_months) : 0))
  const [selection, setSelection] = useState<Selection | null>(() => {
  if (!initialDraft) return null

  const savedProduct = products.find(
    (item) => item.code === initialDraft.product_code,
  )

  if (!savedProduct) return null

  const savedGoal = goals.find(
    (item) =>
      item.code === savedProduct.code &&
      item.label === initialDraft.purpose,
  ) ?? {
    id: 'saved-goal',
    code: savedProduct.code,
    label: initialDraft.purpose,
    description: '',
    icon: 'box',
  }

  return {
    product: savedProduct,
    goal: savedGoal,
    amount: initialDraft.amount,
    months: initialDraft.term_months,
  }
})
  const [result, setResult] = useState<Application | null>(null)
  const goal = available.find((item) => item.id === goalId) ?? available[0]
  const product = products.find((item) => item.code === goal?.code)
  if (!product || !goal) return <div className="credit-notice">Сейчас нет доступных кредитных продуктов.</div>

  if (result) {
    const labels: Record<string, string> = {
      approved: 'Одобрено', counter_offer: 'Новое предложение', needs_info: 'Нужны дополнительные сведения',
      rejected: 'Отказано', closed: 'Закрыта', accepted: 'Предложение принято',
      signed_demo: 'Подписано (демо)', disbursed_demo: 'Кредит выдан (демо)',
    }
    return <section className="credit-result"><span className="neutral-badge">{labels[result.status] ?? 'На рассмотрении'}</span><h1>Заявка {result.number}</h1><p>{result.status === 'rejected' ? 'К сожалению, банк не одобрил заявку на кредит.' : 'Заявка отправлена. Актуальный статус получен от банка.'}</p><ButtonDesktop type="button" view="accent" size={48} onClick={() => navigate('/')}>На главную</ButtonDesktop></section>
  }

  if (
  initialDraft &&
  !products.some((item) => item.code === initialDraft.product_code)
) {
  return (
    <div className="credit-notice">
      Продукт этого черновика сейчас недоступен.
      <Link to="/applications">К списку заявок</Link>
    </div>
  )
}

if (selection) {
  return (
    <ApplicationForm
      selection={{ ...selection, product: products.find((item) => item.code === selection.product.code) ?? { ...selection.product, indicative_rate_min: undefined } }}
      companies={companies}
      session={session}
      initialDraft={initialDraft}
      onBack={() => {
        if (initialDraft) {
          navigate('/applications')
        } else {
          setSelection(null)
        }
      }}
      onDone={(application) => {
  setResult(application)
  navigate(`/applications/${application.id}`)
}}
    />
  )
}

  const amount = Number(amountText)
  const months = Number(monthsText)
  const annualRate = indicativeRate(product.indicative_rate_min)
  const monthlyPayment = indicativePayment(amount, months, annualRate)
  const valid = amountText !== '' && monthsText !== '' && Number.isSafeInteger(amount) && Number.isSafeInteger(months)
    && amount >= product.min_amount && amount <= product.max_amount
    && months >= product.min_term_months && months <= product.max_term_months

  function chooseGoal(next: Goal) {
    const nextProduct = products.find((item) => item.code === next.code)
    if (!nextProduct) return
    setGoalId(next.id)
    setAmountText(String(clamp(Number(amountText) || nextProduct.min_amount, nextProduct.min_amount, nextProduct.max_amount)))
    setMonthsText(String(clamp(Number(monthsText) || nextProduct.min_term_months, nextProduct.min_term_months, nextProduct.max_term_months)))
  }

  return (
    <div className="credit-calculator-content">
      <div className="credit-heading"><h1>Рассчитайте свой кредит</h1><p>Подберите финансирование для вашего бизнеса</p></div>
      <div className="credit-goals" role="group" aria-label="Цель кредита">
        {available.map((item) => <button key={item.id} type="button" className={`credit-goal${item.id === goal.id ? ' is-selected' : ''}`} aria-pressed={item.id === goal.id} onClick={() => chooseGoal(item)}><GoalIcon kind={item.icon} /><span>{item.label}</span></button>)}
      </div>
      <div className="credit-ranges">
        <RangeCard id="credit-amount" label="Сумма кредита" value={amountText} min={product.min_amount} max={product.max_amount} suffix="₽" onChange={setAmountText} />
        <RangeCard id="credit-months" label="Срок кредита" value={monthsText} min={product.min_term_months} max={product.max_term_months} suffix="мес." onChange={setMonthsText} />
      </div>
      <section className="credit-offer" aria-label="Подходящий кредит">
        <div className="credit-offer-description"><h2>{product.name}</h2><p>{goal.description}</p><ul className="credit-benefits"><li><CheckIcon />До {money(product.max_amount)}</li><li><CheckIcon />До {formatTerm(product.max_term_months, true)}</li><li><CheckIcon />Заявка онлайн</li></ul></div>
        <div className="credit-offer-action">
          <div className="credit-offer-numbers"><div><span>Демо-ставка в год</span><strong>{indicativeRateLabel(annualRate)}</strong></div><div aria-live="polite" aria-atomic="true"><span>Ежемесячный платёж</span><strong>{valid && monthlyPayment !== null ? `от ${money(monthlyPayment)}` : '—'}</strong></div></div>
          <ButtonDesktop className="credit-apply-button" type="button" view="accent" size={48} disabled={!valid} onClick={() => setSelection({ goal, product, amount, months })}>Оформить заявку</ButtonDesktop>
          <p className="credit-offer-note">Предварительный расчёт без дополнительных услуг.<br />Итоговые условия — после рассмотрения заявки.</p>
        </div>
      </section>
      <div className="credit-notice"><span aria-hidden="true">ⓘ</span><p>{annualRate === null ? 'Демонстрационный расчёт недоступен: сервер не передал предварительную ставку.' : `Демонстрационная ставка ${indicativeRateLabel(annualRate)} используется для предварительного расчёта. Итоговые условия банк определит после рассмотрения заявки.`}</p></div>
    </div>
  )
}
function CreditCalculator({
  session,
  onLogout,
  initialDraft,
}: Props) {
  const query = useQuery({
    queryKey: ['credit-catalog', session.user.id],
    queryFn: async ({ signal }) => {
      const [products, companies] = await Promise.all([
        api<Product[]>('/products', session.token, { signal }),
        api<Company[]>('/me/companies', session.token, { signal }),
      ])
      return { products, companies }
    },
    staleTime: 30_000, retry: false,
  })
  return (
    <div className="dashboard credit-page">
      <ClientHeader
        session={session}
        onLogout={onLogout}
        company={
          query.data?.companies.find((item) => item.id === initialDraft?.company_id)
          ?? query.data?.companies[0]
          ?? null
        }
        loading={query.isPending}
      />
      <main className="credit-main">
        {query.isPending && <p role="status">Загружаем кредитные продукты…</p>}
        {query.isError && <div className="credit-notice" role="alert"><p>{query.error.message}</p><button type="button" onClick={() => void query.refetch()}>Повторить</button></div>}
        {query.data && <CalculatorBody
  key={initialDraft?.id ?? 'new'}
  products={query.data.products}
  companies={query.data.companies}
  session={session}
  initialDraft={initialDraft}
/>}
      </main>
    </div>
  )
}

export default CreditCalculator
