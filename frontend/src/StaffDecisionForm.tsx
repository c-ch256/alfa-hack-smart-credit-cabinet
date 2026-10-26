import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import type { Session } from './auth'
import type { StaffDetail } from './staffApplicationDetail'
import { rubles } from './staffApplicationDetail'
import { staffProductNames } from './staffApplications'
import { useStaffProducts } from './staffProducts'
import StaffLoanPreview from './StaffLoanPreview'
import { COMMENT_LIMIT, canMakeStaffDecision, counterFieldErrors, groupedAmount, publicInfoRequest, requestedFields, sendStaffDecision, StaffDecisionError } from './staffDecision'
import type { DecisionPayload, ManualDecision, RequestInfoField } from './staffDecision'

type Action = ManualDecision | 'request_info'
const actions: { key: Action; label: string; button: string }[] = [
  { key: 'approve', label: 'Одобрить', button: 'Одобрить заявку' },
  { key: 'counter', label: 'Предложить другие условия', button: 'Отправить предложение' },
  { key: 'reject', label: 'Отказать', button: 'Отказать в кредите' },
  { key: 'request_info', label: 'Запросить сведения', button: 'Отправить запрос' },
]

export default function StaffDecisionForm({ detail, session, onConfirmed, onRefresh }: {
  detail: StaffDetail
  session: Session
  onConfirmed: (app: StaffDetail['application']) => void
  onRefresh: () => Promise<boolean>
}) {
  const app = detail.application
  const [action, setAction] = useState<Action>('approve')
  const [comments, setComments] = useState<Record<Action, string>>({ approve: '', counter: '', reject: '', request_info: '' })
  const [product, setProduct] = useState(app.product_code)
  const [amount, setAmount] = useState(String(app.amount))
  const [term, setTerm] = useState(String(app.term_months))
  const [selectedFields, setSelectedFields] = useState<RequestInfoField[]>([])
  const [error, setError] = useState('')
  const [commentError, setCommentError] = useState('')
  const [pending, setPending] = useState(false)
  const [refreshRequired, setRefreshRequired] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const inFlight = useRef(false)
  const comment = comments[action]
  const publicRequest = publicInfoRequest(selectedFields)
  const missingFields = requestedFields.filter((field) => app.product_code === 'investment' || field.key === 'purpose_details')
  const catalog = useStaffProducts(session)
  const products = catalog.isError ? [] : catalog.data?.products ?? []
  const limits = products.find((item) => item.code === product)
  const approvalLimits = products.find((item) => item.code === app.product_code)
  const fieldErrors = counterFieldErrors(limits, amount, term)
  const approvalErrors = counterFieldErrors(approvalLimits, String(app.amount), String(app.term_months))
  const canDecide = canMakeStaffDecision(detail, session, action)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inFlight.current || refreshRequired || !canMakeStaffDecision(detail, session, action)) return
    setError(''); setCommentError('')
    if (action === 'counter' && (fieldErrors.amount || fieldErrors.term) || action === 'approve' && (approvalErrors.amount || approvalErrors.term)) return
    if (action === 'request_info' && (!selectedFields.length || selectedFields.some((key) => !missingFields.some((field) => field.key === key)))) {
      setError('Выберите сведения, которые нужно дополнить.'); return
    }
    if (!comment.trim() || comment.length > COMMENT_LIMIT) {
      setCommentError(`Укажите внутренний комментарий: от 1 до ${COMMENT_LIMIT} символов.`)
      return
    }
    const payload: DecisionPayload | { fields: RequestInfoField[]; comment: string } = action === 'request_info'
      ? { fields: selectedFields, comment: comment.trim() } : { decision: action, comment: comment.trim() }
    if (action === 'counter' && 'decision' in payload) {
      const nextAmount = Number(amount), nextTerm = Number(term)
      if (!Number.isSafeInteger(nextAmount) || nextAmount <= 0 || !Number.isSafeInteger(nextTerm) || nextTerm <= 0) {
        setError('Укажите сумму и срок целыми положительными числами.'); return
      }
      if (!['working_capital', 'investment'].includes(product)) { setError('Выберите поддерживаемый продукт.'); return }
      if (nextAmount === app.amount && nextTerm === app.term_months && product === app.product_code) {
        setError('Измените продукт, сумму или срок предложения.'); return
      }
      if (nextAmount !== app.amount) payload.amount = nextAmount
      if (nextTerm !== app.term_months) payload.term_months = nextTerm
      if (product !== app.product_code) payload.product_code = product
    }
    inFlight.current = true; setPending(true)
    try {
      const updated = await sendStaffDecision(app.id, session.token, payload)
      onConfirmed(updated)
    } catch (cause) {
      setError(cause instanceof StaffDecisionError ? cause.message : 'Не удалось подтвердить результат. Обновите заявку.')
      setRefreshRequired(!(cause instanceof StaffDecisionError) || cause.refreshRequired)
    } finally { inFlight.current = false; setPending(false) }
  }
  async function refresh() {
    if (refreshing) return
    setRefreshing(true)
    try {
      if (await onRefresh()) { setRefreshRequired(false); setError('') }
    } finally { setRefreshing(false) }
  }
  return <aside className="staff-detail-sidebar" aria-label="Решение по заявке">
    <section className="staff-detail-card staff-decision-card">
      <h2>Решение по заявке</h2>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <fieldset disabled={pending || refreshRequired} className="staff-decision-fields">
          <legend className="visually-hidden">Выберите действие сотрудника</legend>
          <div className="staff-decision-options">{actions.map((item) => <label key={item.key} className={action === item.key ? 'is-selected' : ''}>
            <input type="radio" name={`staff-action-${app.id}`} value={item.key} checked={action === item.key}
              disabled={!canMakeStaffDecision(detail, session, item.key)}
              onChange={() => { setAction(item.key); setError(''); setCommentError('') }} />
            <span>{item.label}</span>
          </label>)}</div>
          <div className="staff-decision-content">
            {(action === 'approve' || action === 'counter') && <>
              <h3>{action === 'approve' ? 'Условия одобрения' : 'Новые условия'}</h3>
              {action === 'counter' ? <>
                <label className="staff-decision-field">Продукт
                  <select disabled={!products.length} value={product} onChange={(event) => setProduct(event.target.value)}>
                    {!products.some((item) => item.code === product) && <option value={product} disabled>{staffProductNames[product] ?? product}</option>}
                    {products.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
                  </select>
                </label>
                <div className="staff-decision-grid">
                  <label className="staff-decision-field">Сумма, ₽<input type="text" inputMode="numeric" autoComplete="off" maxLength={24}
                    value={groupedAmount(amount)} aria-invalid={!!fieldErrors.amount} aria-describedby={`staff-amount-hint-${app.id}`}
                    onChange={(event) => {
                      const input = event.currentTarget
                      if (!/^[\d\s]*$/.test(input.value)) return
                      const digitsBefore = input.value.slice(0, input.selectionStart ?? input.value.length).replace(/\s/g, '').length
                      const next = input.value.replace(/\s/g, '')
                      setAmount(next)
                      requestAnimationFrame(() => {
                        if (document.activeElement !== input) return
                        let position = 0, digits = 0
                        while (position < input.value.length && digits < digitsBefore) {
                          if (/\d/.test(input.value[position])) digits++
                          position++
                        }
                        input.setSelectionRange(position, position)
                      })
                    }} required />
                    <span id={`staff-amount-hint-${app.id}`} className={fieldErrors.amount ? 'staff-decision-error' : 'staff-decision-field-hint'}>
                      {fieldErrors.amount || (limits && `От ${rubles(limits.min_amount)} до ${rubles(limits.max_amount)}`)}
                    </span>
                  </label>
                  <label className="staff-decision-field">Срок, мес.<input type="text" inputMode="numeric" maxLength={4} value={term}
                    aria-invalid={!!fieldErrors.term} aria-describedby={`staff-term-hint-${app.id}`}
                    onChange={(event) => { if (/^\d*$/.test(event.target.value)) setTerm(event.target.value) }} required />
                    <span id={`staff-term-hint-${app.id}`} className={fieldErrors.term ? 'staff-decision-error' : 'staff-decision-field-hint'}>
                      {fieldErrors.term || (limits && `От ${limits.min_term_months} до ${limits.max_term_months} мес.`)}
                    </span>
                  </label>
                </div>
              </> : <dl className="staff-decision-terms">
                <div><dt>Продукт</dt><dd>{approvalLimits?.name ?? staffProductNames[app.product_code] ?? app.product_code}</dd></div>
                <div><dt>Сумма кредита</dt><dd>{rubles(app.amount)}</dd></div>
                <div><dt>Срок</dt><dd>{app.term_months} мес.</dd></div>
              </dl>}
              <StaffLoanPreview key={JSON.stringify([action, product, amount, term, catalog.dataUpdatedAt, catalog.isError])} detail={detail} session={session} action={action}
                product={action === 'approve' ? app.product_code : product}
                amount={action === 'approve' ? String(app.amount) : amount}
                term={action === 'approve' ? String(app.term_months) : term} limits={action === 'approve' ? approvalLimits : limits} />
              {catalog.isPending ? <p className="staff-detail-muted" role="status">Загружаем продукты и ограничения…</p>
                : catalog.isError ? <div className="staff-preview-error"><p className="staff-decision-error" role="alert">Не удалось загрузить каталог продуктов.</p>
                  <button type="button" onClick={() => void catalog.refetch()}>Повторить загрузку</button></div>
                  : !products.length ? <p className="staff-detail-muted">Нет доступных продуктов.</p> : null}
            </>}
            {action === 'reject' && <h3>Обоснование отказа</h3>}
            {action === 'request_info' && <>
              <h3>Какие сведения нужны</h3>
              <div className="staff-decision-missing">{missingFields.map((field) => <label key={field.key}>
                <input type="checkbox" checked={selectedFields.includes(field.key)} onChange={(event) => setSelectedFields((current) =>
                  event.target.checked ? [...current, field.key] : current.filter((key) => key !== field.key))} />{field.label}
              </label>)}</div>
              <div className="staff-decision-public-preview"><h3>Запрос клиенту</h3>
                <p>{publicRequest || 'Выберите сведения — текст запроса сформируется автоматически.'}</p>
              </div>
            </>}
            <label className="staff-decision-field">Внутренний комментарий <span className="staff-decision-required">*</span>
              <textarea value={comment} maxLength={COMMENT_LIMIT} required rows={4} aria-invalid={!!commentError}
                aria-describedby={`staff-comment-hint-${app.id}${commentError ? ` staff-comment-error-${app.id}` : ''}`}
                onChange={(event) => { setComments((current) => ({ ...current, [action]: event.target.value })); setCommentError('') }} />
            </label>
            <div className="staff-decision-comment-meta"><span id={`staff-comment-hint-${app.id}`}>Видно только сотрудникам банка</span><span>{comment.length}/{COMMENT_LIMIT}</span></div>
            {commentError && <p className="staff-decision-error" id={`staff-comment-error-${app.id}`} role="alert">{commentError}</p>}
          </div>
        </fieldset>
        {!canDecide && <p className="staff-decision-blocked" id={`staff-assignment-blocked-${app.id}`}>
          {app.assigned_staff_user_id === undefined || !Array.isArray(detail.allowed_actions)
            ? 'Отправка недоступна: сервер не подтвердил право действия. Обновите данные заявки.'
            : 'Сервер не разрешает это действие текущему сотруднику для этой заявки.'}
        </p>}
        {error && <p className="staff-decision-error" role="alert">{error}</p>}
        {refreshRequired && <ButtonDesktop type="button" view="secondary" size={40} block loading={refreshing} onClick={() => void refresh()}>Обновить данные заявки</ButtonDesktop>}
        <ButtonDesktop type="submit" view="accent" size={40} block className="staff-decision-submit" loading={pending}
          disabled={pending || refreshRequired || !canDecide
            || action === 'counter' && (!limits || !!fieldErrors.amount || !!fieldErrors.term)
            || action === 'approve' && (!approvalLimits || !!approvalErrors.amount || !!approvalErrors.term)
            || action === 'request_info' && !selectedFields.length}
          aria-describedby={!canDecide ? `staff-assignment-blocked-${app.id}` : undefined}>
          {actions.find((item) => item.key === action)?.button}
        </ButtonDesktop>
      </form>
    </section>
  </aside>
}
