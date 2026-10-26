import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import type { Session } from './auth'
import ClientHeader from './ClientHeader'
import type { CabinetCompany } from './ClientHeader'
import ApplicationDocuments from './ApplicationDocuments'
import ClientAdditionalInfoForm from './ClientAdditionalInfoForm'
import type { InfoApplication, InfoPayload } from './clientAdditionalInfo'
import { contractQueryOptions, nextScheduledPayment, paymentDate } from './creditDocuments'
import './Dashboard.css'
import './CreditCalculator.css'
import './ApplicationPage.css'

type Application = InfoApplication & {
  id: number
  number: string
  company_id: number
  product_code: string
  status: string
  amount: number
  term_months: number
  purpose: string
  purpose_details: string | null
  investment_object: string | null
  collateral_type: string | null
  collateral_value: number | null
  info_request: string | null
  version: number
  created_at: string
  updated_at: string
}

type Offer = {
  id: number
  product_code: string
  amount: number
  term_months: number
  rate: string | number
  monthly_payment: number
  status: string
}

type StatusEvent = {
  sequence: number
  from_status: string | null
  to_status: string
  created_at: string
}

type Company = CabinetCompany
type Product = { code: string; name: string }
type DemoCode = { code: string; expires_at: string }
type Data = {
  application: Application
  offers: Offer[]
  history: StatusEvent[]
  companies: Company[]
  products: Product[]
}
type Props = { session: Session; onLogout: () => void }
type Tone = 'success' | 'info' | 'warning' | 'danger' | 'neutral'
type IconName =
  | 'check'
  | 'clock'
  | 'file'
  | 'loader'
  | 'minus'
  | 'chevron'
  | 'back'
  | 'bell'
  | 'close'
  | 'info'
type Confirmation = { path: string; title: string; description: string }
type View = {
  label: string
  title: string
  description: string
  tone: Tone
  icon: IconName
}

const processingStatuses = new Set([
  'submitted',
  'checking',
  'scoring',
  'decision',
  'manual_review',
])
const stepNames = ['Заявка', 'Рассмотрение', 'Решение', 'Подписание', 'Выдача']
// Same demonstration rate as CreditCalculator. Not a saved quote or a bank tariff.
const PRELIMINARY_DEMO_RATE = 23.5
function preliminaryPayment(amount: number, months: number) {
  if (amount <= 0 || months <= 0) return 0
  const rate = PRELIMINARY_DEMO_RATE / 1200
  return Math.round((amount * rate) / (1 - (1 + rate) ** -months))
}
const numberFormat = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
})

function money(value: number) {
  return Number.isFinite(value) ? `${numberFormat.format(value)} ₽` : '—'
}

function dateOnly(value: string) {
  const timestamp = new Date(value)
  return Number.isNaN(timestamp.getTime())
    ? '—'
    : timestamp.toLocaleDateString('ru-RU')
}

function dateTime(value: string) {
  const timestamp = new Date(value)
  return Number.isNaN(timestamp.getTime())
    ? '—'
    : timestamp.toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
}

function annualRate(value: string | number) {
  const numeric = Number(value)
  return Number.isFinite(numeric)
    ? `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(numeric)}% годовых`
    : '—'
}

function term(months: number) {
  if (!Number.isInteger(months) || months <= 0) return '—'
  const years = Math.floor(months / 12)
  const rest = months % 12
  return [
    years
      ? new Intl.NumberFormat('ru-RU', {
          style: 'unit',
          unit: 'year',
          unitDisplay: 'long',
        }).format(years)
      : '',
    rest
      ? new Intl.NumberFormat('ru-RU', {
          style: 'unit',
          unit: 'month',
          unitDisplay: 'long',
        }).format(rest)
      : '',
  ]
    .filter(Boolean)
    .join(' ')
}

function stage(status: string) {
  if (status === 'draft') return 0
  if (status === 'signed_demo' || status === 'disbursed_demo') return 4
  if (status === 'accepted') return 3
  if (['approved', 'counter_offer', 'rejected'].includes(status)) return 2
  return 1
}

function getView(status: string, declined: boolean): View {
  const views: Record<string, View> = {
    draft: {
      label: 'Черновик',
      title: 'Заявка сохранена',
      description:
        'Продолжите заполнение и отправьте заявку на рассмотрение банка.',
      tone: 'neutral',
      icon: 'file',
    },
    needs_info: {
      label: 'Нужны сведения',
      title: 'Дополните заявку',
      description:
        'Банк запросил дополнительные сведения. Передайте их, чтобы продолжить рассмотрение.',
      tone: 'warning',
      icon: 'file',
    },
    approved: {
      label: 'Одобрено',
      title: 'Ваша заявка одобрена',
      description: 'Банк одобрил заявку. Ознакомьтесь с условиями предложения.',
      tone: 'success',
      icon: 'check',
    },
    counter_offer: {
      label: 'Новое предложение',
      title: 'Банк предлагает другие условия',
      description:
        'Посмотрите предложение и выберите, подходит ли оно вашему бизнесу.',
      tone: 'warning',
      icon: 'file',
    },
    accepted: {
      label: 'Ожидает подписания',
      title: 'Предложение принято',
      description:
        'Проверьте согласованные условия и подтвердите демо-подписание кодом.',
      tone: 'success',
      icon: 'check',
    },
    signed_demo: {
      label: 'Оформляется выдача',
      title: 'Договор подписан',
      description:
        'Подписание выполнено в демонстрационном режиме. Ожидаем подтверждения демо-выдачи.',
      tone: 'warning',
      icon: 'loader',
    },
    disbursed_demo: {
      label: 'Кредит выдан (демо)',
      title: 'Кредит выдан',
      description: 'Выдача выполнена в демонстрационном режиме.',
      tone: 'success',
      icon: 'check',
    },
    rejected: {
      label: 'Отказано',
      title: 'По вашей заявке принято отрицательное решение',
      description: 'Рассмотрение заявки завершено.',
      tone: 'neutral',
      icon: 'file',
    },
    closed: {
      label: 'Закрыта',
      title: declined ? 'Вы отказались от предложения банка' : 'Заявка закрыта',
      description: declined
        ? 'Заявка закрыта. Договор не подписан, денежные средства не перечислены.'
        : 'Оформление по этой заявке завершено.',
      tone: 'neutral',
      icon: 'file',
    },
  }
  return (
    views[status] ?? {
      label: 'На рассмотрении',
      title: 'Рассматриваем вашу заявку',
      description:
        'Проверяем предоставленные сведения. Дополнительных действий с вашей стороны сейчас не требуется. Решение появится в кабинете.',
      tone: 'info',
      icon: 'clock',
    }
  )
}

function publicEvent(event: StatusEvent, declined: boolean) {
  const labels: Record<string, string> = {
    draft: 'Черновик сохранён',
    submitted: 'Заявка отправлена',
    checking: 'Заявка принята на рассмотрение',
    scoring: 'Заявка принята на рассмотрение',
    decision: 'Заявка принята на рассмотрение',
    manual_review: 'Заявка принята на рассмотрение',
    needs_info: 'Банк запросил дополнительные сведения',
    approved: 'Заявка одобрена',
    counter_offer: 'Банк предложил другие условия',
    accepted: 'Вы приняли предложение банка',
    signed_demo: 'Договор подписан (демо)',
    disbursed_demo: 'Кредит выдан (демо)',
    rejected: 'По заявке принято отрицательное решение',
    closed: declined
      ? 'Вы отказались от предложения банка'
      : ['draft', 'needs_info'].includes(event.from_status ?? '')
        ? 'Заявка отозвана'
        : 'Заявка закрыта',
  }
  return labels[event.to_status] ?? 'Статус заявки обновлён'
}

function visibleHistory(events: StatusEvent[], declined: boolean) {
  const result: { sequence: number; title: string; createdAt: string }[] = []
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    const title = publicEvent(event, declined)
    // Технические подэтапы объединяем в одно клиентское событие.
    if (result[result.length - 1]?.title === title) continue
    result.push({
      sequence: event.sequence,
      title,
      createdAt: event.created_at,
    })
  }
  return result.reverse()
}

class ApiError extends Error {
  code: string
  constructor(message: string, code = '') {
    super(message)
    this.code = code
  }
}

async function request<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  if (init.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...init, headers }).catch(
    (cause: unknown) => {
      if (init.signal?.aborted) throw cause
      throw new ApiError('Нет соединения с сервером. Попробуйте ещё раз.')
    },
  )
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { code?: string }
    const safeErrors: Record<string, string> = {
      INVALID_CODE: 'Неверный код. Проверьте цифры и повторите ввод.',
      CODE_EXPIRED: 'Срок действия кода истёк. Получите новый код.',
      CODE_ALREADY_ISSUED:
        'Код уже выдан. Введите его или дождитесь окончания срока действия, чтобы получить новый.',
      CODE_ATTEMPTS_EXCEEDED:
        'Лимит попыток исчерпан. Дождитесь окончания срока кода и получите новый.',
      CODE_NOT_ISSUED: 'Сначала получите код подтверждения.',
      MISSING_PRODUCT_DATA:
        'Для принятия предложения не хватает сведений о выбранном продукте.',
    }
    const fallback =
      response.status === 401
        ? 'Сессия завершена. Выйдите и войдите снова.'
        : response.status === 404
          ? 'Заявка или предложение не найдены.'
          : response.status === 409
            ? 'Состояние заявки изменилось. Проверьте обновлённые данные.'
            : response.status === 422
              ? 'Проверьте заполненные данные.'
              : 'Не удалось выполнить запрос. Попробуйте ещё раз.'
    // Тексты внутренних ошибок и причины решений в клиентский интерфейс не передаём.
    throw new ApiError(safeErrors[body.code ?? ''] ?? fallback, body.code)
  }
  return response.json() as Promise<T>
}

async function loadHistory(id: number, token: string, signal: AbortSignal) {
  const events: StatusEvent[] = []
  for (let offset = 0; ; offset += 100) {
    const page = await request<StatusEvent[]>(
      `/applications/${id}/history?limit=100&offset=${offset}`,
      token,
      { signal },
    )
    events.push(...page)
    if (page.length < 100) return events
  }
}

function Icon({
  name,
  className = '',
}: {
  name: IconName
  className?: string
}) {
  const paths: Partial<Record<IconName, string>> = {
    check: 'm6 12 4 4 8-8',
    file: 'M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5ZM14 3v5h5',
    minus: 'M6 12h12',
    chevron: 'm6 9 6 6 6-6',
    back: 'm14 6-6 6 6 6M8 12h13',
    close: 'm6 6 12 12M18 6 6 18',
    bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4',
  }
  return (
    <svg
      className={className}
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {name === 'clock' ? (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 6v6l4 2" />
        </>
      ) : name === 'loader' ? (
        <circle cx="12" cy="12" r="9" strokeDasharray="2 4" />
      ) : name === 'info' ? (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v6M12 7h.01" />
        </>
      ) : (
        <path d={paths[name]} />
      )}
    </svg>
  )
}

function Disclosure({
  title,
  expanded,
  resetKey,
  children,
}: {
  title: string
  expanded: boolean
  resetKey: string
  children: ReactNode
}) {
  const disclosureKey = `${resetKey}:${expanded}`

const [toggle, setToggle] = useState<{
  key: string
  open: boolean
} | null>(null)

const open =
  toggle?.key === disclosureKey ? toggle.open : expanded

const contentId = useId()
  return (
    <section className="request-card request-disclosure">
      <button
        type="button"
        className="request-disclosure-toggle"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() =>
  setToggle({
    key: disclosureKey,
    open: !open,
  })
}
      >
        <span>{title}</span>
        <Icon name="chevron" />
      </button>
      <div id={contentId} hidden={!open} className="request-disclosure-content">
        {children}
      </div>
    </section>
  )
}

function CreditMetrics({ offer }: { offer: Offer }) {
  return (
    <div className="request-credit-metrics">
      <div>
        <span>Сумма кредита</span>
        <strong>{money(offer.amount)}</strong>
      </div>
      <div>
        <span>Срок</span>
        <strong>{term(offer.term_months)}</strong>
      </div>
      <div>
        <span>Ставка</span>
        <strong>{annualRate(offer.rate)}</strong>
      </div>
      <div>
        <span>Платёж в месяц</span>
        <strong>{money(offer.monthly_payment)}</strong>
      </div>
    </div>
  )
}

function ApplicationCard({
  session,
  onLogout,
  id,
}: Props & { id: number | null }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const busyRef = useRef(false)
  const [error, setError] = useState('')
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const [consent, setConsent] = useState(false)
  const [issuedCode, setIssuedCode] = useState<DemoCode | null>(null)
  const codeInput = useRef<HTMLInputElement>(null)
  const focusCodeAfterIssue = useRef(false)
  const [enteredCode, setEnteredCode] = useState('')
  const [showCodeInput, setShowCodeInput] = useState(false)
  const [codeBlocked, setCodeBlocked] = useState(false)
  const [issuancePreview, setIssuancePreview] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const busy = busyAction !== null

  const query = useQuery<Data>({
    queryKey: ['application-detail', session.user.id, id],
    enabled: id !== null,
    queryFn: async ({ signal }) => {
      const application = await request<Application>(
        `/applications/${id}`,
        session.token,
        {
          signal,
        },
      )
      const [offers, history, companies, products] = await Promise.all([
        request<Offer[]>(
          `/applications/${application.id}/offers`,
          session.token,
          { signal },
        ),
        loadHistory(application.id, session.token, signal),
        request<Company[]>('/me/companies', session.token, { signal }),
        request<Product[]>('/products', session.token, { signal }),
      ])
      return { application, offers, history, companies, products }
    },
    staleTime: 0,
    retry: false,
    refetchInterval: (state) =>
      processingStatuses.has(state.state.data?.application.status ?? '') ||
      state.state.data?.application.status === 'signed_demo'
        ? 5000
        : false,
  })

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (confirmation && !element.open) element.showModal()
    if (!confirmation && element.open) element.close()
  }, [confirmation])

  useEffect(() => {
    if (!issuedCode || query.data?.application.status !== 'accepted') return
    const timer = setInterval(() => {
      const timestamp = Date.now()
      setNow(timestamp)
      if (timestamp >= Date.parse(issuedCode.expires_at)) clearInterval(timer)
    }, 1000)
    return () => clearInterval(timer)
  }, [issuedCode, query.data?.application.status])

  useEffect(() => {
    if (busy || !showCodeInput || query.data?.application.status !== 'accepted') return
    const input = codeInput.current
    if (focusCodeAfterIssue.current && input && !input.disabled) {
      input.focus()
      focusCodeAfterIssue.current = false
    }
  }, [busy, showCodeInput, issuedCode, query.data?.application.status])

  function receiveDemoCode(code: DemoCode) {
    focusCodeAfterIssue.current = true
    setIssuedCode(code)
    setNow(Date.now())
    setEnteredCode('')
    setCodeBlocked(false)
    setShowCodeInput(true)
  }

  async function runAction<T extends Application | DemoCode>(
    action: string,
    path: string,
    body?: unknown,
    onSuccess?: (value: T) => void,
  ) {
    if (busyRef.current) return false
    busyRef.current = true
    setBusyAction(action)
    setError('')
    try {
      const result = await request<T>(path, session.token, {
        method: 'POST',
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      })
      const value: Application | DemoCode = result
      const showIssuance =
        action === 'sign' && 'id' in value && value.status === 'disbursed_demo'
      if (showIssuance) setIssuancePreview(true)
      const previewStartedAt = Date.now()
      if ('id' in value) {
        client.setQueryData<Data>(
          ['application-detail', session.user.id, id],
          (previous) =>
            previous ? { ...previous, application: value } : previous,
        )
        if (value.status !== 'accepted') {
          setIssuedCode(null)
          setEnteredCode('')
          setConsent(false)
        }
      }
      onSuccess?.(result)
      void client.invalidateQueries({
        queryKey: ['my-applications', session.user.id],
      })
      void client.invalidateQueries({
        queryKey: ['dashboard', session.user.id],
      })
      if (action === 'information') {
        void client.invalidateQueries({ queryKey: ['staff-applications'] })
        void client.invalidateQueries({ queryKey: ['staff-application'] })
      }
      await query.refetch()
      if (showIssuance) {
        const remaining = 2000 - (Date.now() - previewStartedAt)
        if (remaining > 0)
          await new Promise<void>((resolve) => setTimeout(resolve, remaining))
      }
      return true
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Не удалось выполнить действие.',
      )
      if (cause instanceof ApiError && cause.code === 'CODE_ATTEMPTS_EXCEEDED')
        setCodeBlocked(true)
      if (cause instanceof ApiError && cause.code === 'CODE_ALREADY_ISSUED')
        setShowCodeInput(true)
      await query.refetch()
      return false
    } finally {
      setIssuancePreview(false)
      busyRef.current = false
      setBusyAction(null)
    }
  }

  const data = query.data
  // Visual transition only: never write a synthetic status to the API or query cache.
  const application =
    issuancePreview && data?.application.status === 'disbursed_demo'
      ? { ...data.application, status: 'signed_demo' }
      : data?.application
  const sortedOffers = [...(data?.offers ?? [])].sort((a, b) => b.id - a.id)
  const latestOffer = sortedOffers[0]
  const acceptedOffer = sortedOffers.find(
    (offer) => offer.status === 'accepted',
  )
  const contractRevision = data?.application.status ?? ''
  const issuedContract = useQuery({
    ...contractQueryOptions({
      offerId: acceptedOffer?.id ?? 0,
      applicationId: id ?? 0,
      userId: session.user.id,
      token: session.token,
      revision: contractRevision,
    }),
    enabled: Boolean(acceptedOffer && data?.application.status === 'disbursed_demo'),
  })
  const nextPayment = issuedContract.data
    ? nextScheduledPayment(issuedContract.data.schedule)
    : undefined
  const declined =
    application?.status === 'closed' && latestOffer?.status === 'declined'
  const proposal =
    application &&
    ['approved', 'counter_offer'].includes(application.status) &&
    latestOffer?.status === 'proposed'
      ? latestOffer
      : undefined
  const view = getView(application?.status ?? 'draft', declined)
  const company = data?.companies.find(
    (item) => item.id === application?.company_id,
  )
  const productName = (code: string) =>
    data?.products.find((product) => product.code === code)?.name ??
    (code === 'investment'
      ? 'Инвестиционный кредит'
      : code === 'working_capital'
        ? 'Оборотный кредит'
        : 'Кредит')
  const stopped =
    application?.status === 'rejected' || application?.status === 'closed'
  const completed = application?.status === 'disbursed_demo'
  const currentStep =
    application?.status === 'closed'
      ? Math.max(
          0,
          ...(data?.history ?? [])
            .filter((event) => event.to_status !== 'closed')
            .map((event) => stage(event.to_status)),
        )
      : stage(application?.status ?? 'draft')
  const history = visibleHistory(
    (data?.history ?? []).filter(
      (event) => !issuancePreview || event.to_status !== 'disbursed_demo',
    ),
    declined,
  )
  const agreedOffer =
    application &&
    ['accepted', 'signed_demo', 'disbursed_demo'].includes(application.status)
      ? acceptedOffer
      : undefined
  const expandedByDefault =
    processingStatuses.has(application?.status ?? '') || stopped
  const codeAlive =
    issuedCode !== null && Date.parse(issuedCode.expires_at) > now
  const codeExpired = issuedCode !== null && !codeAlive
  const secondsLeft = issuedCode
  ? Math.max(
      0,
      Math.ceil((Date.parse(issuedCode.expires_at) - now) / 1000),
    )
  : 0

const codeCountdown = [
  Math.floor(secondsLeft / 60),
  secondsLeft % 60,
]
  .map((value) => String(value).padStart(2, '0'))
  .join(':')
  const issuedAt =
    data?.history.find((event) => event.to_status === 'disbursed_demo')
      ?.created_at ?? application?.updated_at

  function openConfirmation(path: string, title: string, description: string) {
    setError('')
    setConfirmation({ path, title, description })
  }

  function stepDescription(index: number) {
    if (!application) return ''
    if (index === 0) return dateOnly(application.created_at)
    if (stopped && index > currentStep)
      return application.status === 'rejected'
        ? 'Недоступно — отказ'
        : 'Не выполняется'
    if (index === currentStep && stopped)
      return application.status === 'rejected'
        ? 'Отказано'
        : declined
          ? 'Предложение отклонено'
          : 'Заявка закрыта'
    if (index === currentStep && !completed) return 'Текущий этап'
    return ''
  }

  async function sendInformation(payload: InfoPayload) {
    if (!application) return false
    return runAction<Application>(
      'information', `/applications/${application.id}/additional-info`, payload,
    )
  }

  const offerActions = (offer: Offer, stacked = false) => (
    <>
      <div className={`request-offer-actions ${stacked ? 'is-stacked' : ''}`}>
        <ButtonDesktop
          type="button"
          view="accent"
          size={48}
          block
          disabled={busy}
          loading={busyAction === 'accept'}
          onClick={() =>
            void runAction<Application>('accept', `/offers/${offer.id}/accept`)
          }
        >
          Принять предложение
        </ButtonDesktop>
        <ButtonDesktop
          type="button"
          view="secondary"
          size={48}
          block
          className="request-outline-button"
          disabled={busy}
          onClick={() =>
            openConfirmation(
              `/offers/${offer.id}/decline`,
              'Отказаться от предложения?',
              'Заявка будет закрыта. Договор не будет подписан. При необходимости вы сможете создать новую заявку.',
            )
          }
        >
          Отказаться от предложения
        </ButtonDesktop>
      </div>
      <p className="request-footnote">
        {stacked
          ? 'После принятия вы перейдёте к подписанию.'
          : 'Принятие предложения не означает подписание договора.'}
      </p>
    </>
  )

  return (
    <div className="dashboard credit-page request-page">
      <ClientHeader
        session={session}
        company={company ?? null}
        loading={query.isPending}
        onLogout={onLogout}
      />
      <main className="request-main">
        <Link to="/applications" className="request-back">
          <Icon name="back" />
          Вернуться к моим заявкам
        </Link>
        {!application ? (
          <section
            className="request-card request-state"
            role={id === null || query.isError ? 'alert' : 'status'}
          >
            <h1>
              {id === null
                ? 'Некорректный номер заявки'
                : query.isError
                  ? 'Не удалось открыть заявку'
                  : 'Загружаем заявку…'}
            </h1>
            {query.isError && (
              <>
                <p>{query.error.message}</p>
                <ButtonDesktop
                  type="button"
                  view="secondary"
                  size={40}
                  onClick={() => void query.refetch()}
                >
                  Повторить
                </ButtonDesktop>
              </>
            )}
          </section>
        ) : (
          <>
            <div className="request-heading">
              <div>
                <h1>
                  {productName(
                    acceptedOffer?.product_code ?? application.product_code,
                  )}
                </h1>
                <p>
                  Заявка № {application.number} от{' '}
                  {dateOnly(application.created_at)}
                </p>
              </div>
              <span
                className={`request-status request-status--${application.status === 'rejected' ? 'danger' : application.status === 'closed' || application.status === 'draft' ? 'neutral' : application.status === 'accepted' ? 'warning' : view.tone}`}
              >
                {completed && <Icon name="check" />}
                {view.label}
              </span>
            </div>

            <section
              className={`request-card request-stages ${stopped ? 'is-stopped' : ''}`}
              aria-label="Этапы оформления"
            >
              <h2>Этапы оформления</h2>
              <ol className="request-steps">
                {stepNames.map((label, index) => {
                  const done = completed || index < currentStep
                  const current = !completed && index === currentStep
                  const unavailable = stopped && index > currentStep
                  return (
                    <li
                      key={label}
                      className={`${done ? 'is-done' : ''} ${current ? 'is-current' : ''} ${unavailable ? 'is-unavailable' : ''}`}
                      aria-current={current ? 'step' : undefined}
                    >
                      <span className="request-step-marker">
                        {done || (current && stopped) ? (
                          <Icon name="check" />
                        ) : unavailable ? (
                          <Icon name="minus" />
                        ) : (
                          index + 1
                        )}
                      </span>
                      <strong>{label}</strong>
                      <small>{stepDescription(index) || '\u00a0'}</small>
                    </li>
                  )
                })}
              </ol>
              <div
                className={`request-notice request-notice--${view.tone}`}
                role="status"
              >
                <span className="request-notice-icon">
                  <Icon
                    name={view.icon}
                    className={
                      view.icon === 'loader' ? 'request-spinner' : undefined
                    }
                  />
                </span>
                <div>
                  <strong>{view.title}</strong>
                  <p>{view.description}</p>
                </div>
              </div>
              {stopped && (
                <ButtonDesktop
                  type="button"
                  view="secondary"
                  size={48}
                  className="request-outline-button request-return-button"
                  onClick={() => navigate('/applications')}
                >
                  К моим заявкам
                </ButtonDesktop>
              )}
            </section>

            {!confirmation &&
  (error || query.isError) &&
  !(application.status === 'accepted' && showCodeInput) && (
              <div className="request-error" role="alert">
                {error || query.error?.message}
                {query.isError && (
                  <button type="button" onClick={() => void query.refetch()}>
                    Обновить
                  </button>
                )}
              </div>
            )}

            {application.status === 'draft' && (
              <section className="request-card">
                <h2>Продолжить оформление</h2>
                <p className="request-subtitle">
                  Откройте анкету, проверьте данные и отправьте заявку на
                  рассмотрение.
                </p>
                <div className="request-actions">
                  <ButtonDesktop
                    type="button"
                    view="accent"
                    size={48}
                    onClick={() =>
                      navigate(`/applications/${application.id}/edit`)
                    }
                  >
                    Продолжить заполнение
                  </ButtonDesktop>
                </div>
              </section>
            )}

            {proposal && application.status === 'approved' && (
              <section className="request-card request-offer">
                <h2>Условия кредита</h2>
                <CreditMetrics offer={proposal} />
                {proposal.product_code !== application.product_code && (
                  <p className="request-subtitle">
                    Продукт предложения: {productName(proposal.product_code)}
                  </p>
                )}
                {offerActions(proposal)}
              </section>
            )}

            {proposal && application.status === 'counter_offer' && (
              <section className="request-card request-comparison">
                <div className="request-comparison-main">
                  <h2>Предложение банка</h2>
                  <div className="request-table-scroll">
                    <table className="request-comparison-table">
                      <thead>
                        <tr>
                          <th scope="col">Параметр</th>
                          <th scope="col">Вы запрашивали</th>
                          <th scope="col">Банк предлагает</th>
                        </tr>
                      </thead>
                      <tbody>
                        {proposal.product_code !== application.product_code && (
                          <tr>
                            <th scope="row">Продукт</th>
                            <td>{productName(application.product_code)}</td>
                            <td className="is-changed">
                              <span>{productName(proposal.product_code)}</span>
                              <small>Изменено</small>
                            </td>
                          </tr>
                        )}
                        <tr>
                          <th scope="row">Сумма</th>
                          <td>{money(application.amount)}</td>
                          <td
                            className={
                              proposal.amount !== application.amount
                                ? 'is-changed'
                                : ''
                            }
                          >
                            <span>{money(proposal.amount)}</span>
                            {proposal.amount !== application.amount && (
                              <small>Изменено</small>
                            )}
                          </td>
                        </tr>
                        <tr>
                          <th scope="row">Срок</th>
                          <td>{term(application.term_months)}</td>
                          <td
                            className={
                              proposal.term_months !== application.term_months
                                ? 'is-changed'
                                : ''
                            }
                          >
                            <span>{term(proposal.term_months)}</span>
                            {proposal.term_months !==
                              application.term_months && (
                              <small>Изменено</small>
                            )}
                          </td>
                        </tr>
                        <tr>
                          <th scope="row">Ставка</th>
                          <td>
                            от {annualRate(PRELIMINARY_DEMO_RATE)}{' '}
                            <small className="request-demo-label">демо</small>
                          </td>
                          <td>{annualRate(proposal.rate)}</td>
                        </tr>
                        <tr>
                          <th scope="row">Платёж в месяц</th>
                          <td>
                            от{' '}
                            {money(
                              preliminaryPayment(
                                application.amount,
                                application.term_months,
                              ),
                            )}
                          </td>
                          <td>{money(proposal.monthly_payment)}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <p className="request-footnote">
                    В колонке «Вы запрашивали» — предварительный расчёт по
                    текущей демонстрационной ставке калькулятора. Точные условия
                    — в предложении банка.
                  </p>
                </div>
                <div className="request-comparison-result">
                  <h2>Итоговые условия</h2>
                  <dl>
                    <div>
                      <dt>Платёж в месяц</dt>
                      <dd>{money(proposal.monthly_payment)}</dd>
                    </div>
                    <div>
                      <dt>Ставка</dt>
                      <dd>{annualRate(proposal.rate)}</dd>
                    </div>
                    <div>
                      <dt>Срок</dt>
                      <dd>{term(proposal.term_months)}</dd>
                    </div>
                  </dl>
                  {offerActions(proposal, true)}
                </div>
              </section>
            )}

            {acceptedOffer &&
              ['accepted', 'signed_demo'].includes(application.status) && (
                <section className="request-card request-offer">
                  <h2>Согласованные условия</h2>
                  <CreditMetrics offer={acceptedOffer} />
                </section>
              )}

            {application.status === 'needs_info' && (
              <section className="request-card">
                <h2>Дополните заявку</h2>
                {application.info_request && (
                  <p className="request-info-request">
                    {application.info_request}
                  </p>
                )}
                <ClientAdditionalInfoForm
                  key={`${application.id}:${application.active_info_request?.request_event_id ?? 'unknown'}`}
                  application={application}
                  busy={busy}
                  loading={busyAction === 'information'}
                  onSubmit={sendInformation}
                  onWithdraw={() => openConfirmation(
                    `/applications/${application.id}/withdraw`,
                    'Отозвать заявку?',
                    'Заявка будет закрыта. При необходимости вы сможете создать новую заявку.',
                  )}
                />
              </section>
            )}

            {acceptedOffer &&
              ['accepted', 'signed_demo', 'disbursed_demo'].includes(application.status) && (
                <section className="request-card">
                  <h2>Документы по кредиту</h2>
                  <ApplicationDocuments
                    key={`${acceptedOffer.id}:${data?.application.status}`}
                    offerId={acceptedOffer.id}
                    applicationId={application.id}
                    token={session.token}
                    userId={session.user.id}
                    revision={contractRevision}
                  />
                </section>
              )}

            {application.status === 'accepted' && (
              <section className="request-card request-signing">
                <h2>Подписание договора</h2>
                <label className="request-consent">
                  <input
                    type="checkbox"
                    checked={consent}
                    disabled={busy}
                    onChange={(event) => setConsent(event.target.checked)}
                  />
                  <span>
                    Подтверждаю согласие с согласованными условиями
                    демонстрационного договора
                  </span>
                </label>
                {!showCodeInput ? (
                  <div className="request-sign-start">
                    <ButtonDesktop
                      type="button"
                      view="accent"
                      size={48}
                      className="request-compact-button"
                      disabled={busy || !consent}
                      loading={busyAction === 'code'}
                      onClick={() =>
                        void runAction<DemoCode>(
                          'code',
                          `/applications/${application.id}/sign-demo/code`,
                          undefined,
                          receiveDemoCode,
                        )
                      }
                    >
                      Получить код
                    </ButtonDesktop>
                    <button
                      type="button"
                      className="request-text-button"
                      disabled={busy}
                      onClick={() => setShowCodeInput(true)}
                    >
                      У меня уже есть код
                    </button>
                  </div>
                ) : (
                  <>
                    {issuedCode && (
                      <div className="request-code-notice" role="status">
                        <span>
                          {codeAlive ? (
                            <>
                              Демо-код: <strong>{issuedCode.code}</strong>
                            </>
                          ) : (
                            'Срок действия кода истёк'
                          )}
                        </span>
                        <small>
                          {codeAlive
                            ? `Код действует ещё ${codeCountdown}`
                            : 'Получите новый код'}
                        </small>
                      </div>
                    )}
                    <form
                      className="request-sign-row"
                      onSubmit={(event) => {
                        event.preventDefault()
                        if (
                          consent &&
                          /^[0-9]{6}$/.test(enteredCode) &&
                          !codeBlocked &&
                          !codeExpired
                        )
                          void runAction<Application>(
                            'sign',
                            `/applications/${application.id}/sign-demo`,
                            { code: enteredCode },
                          )
                      }}
                    >
                      <label htmlFor="request-sign-code">
                        Код подтверждения
                      </label>
                      <input
                        id="request-sign-code"
                        ref={codeInput}
                        aria-invalid={Boolean(error)}
aria-describedby={error ? 'request-code-error' : undefined}
className={
  error.startsWith('Неверный код') ? 'request-code-shake' : undefined
}
                        required
                        pattern="[0-9]{6}"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        maxLength={6}
                        value={enteredCode}
                        disabled={busy}
                        onChange={(event) => {
  setEnteredCode(event.target.value.replace(/\D/g, ''))
  setError('')
}}
                      />
                      <ButtonDesktop
                        type="submit"
                        view="accent"
                        size={48}
                        className="request-compact-button"
                        disabled={
                          busy ||
                          !consent ||
                          enteredCode.length !== 6 ||
                          codeBlocked ||
                          codeExpired
                        }
                        loading={busyAction === 'sign'}
                      >
                        Подписать договор
                      </ButtonDesktop>
                    </form>
                    {error && (
  <p
    id="request-code-error"
    className="request-code-error"
    role="alert"
  >
    {error}
  </p>
)}
                    <button
                      type="button"
                      className="request-text-button request-code-repeat"
                      disabled={busy || codeAlive || !consent}
                      onClick={() =>
                        void runAction<DemoCode>(
                          'code',
                          `/applications/${application.id}/sign-demo/code`,
                          undefined,
                          receiveDemoCode,
                        )
                      }
                    >
                      Получить новый код
                    </button>
                  </>
                )}
                <p className="request-footnote">
                  Демо-подписание: реальная интеграция с электронной подписью не
                  подключена. СМС не отправляется.
                </p>
              </section>
            )}

            {application.status === 'disbursed_demo' && (
              <section className="request-card">
                <h2>Выдача кредита</h2>
                <div className="request-credit-metrics">
  <div>
    <span>Сумма выдачи</span>
    <strong>
      {money(acceptedOffer?.amount ?? application.amount)}
    </strong>
  </div>

  <div>
    <span>Дата выдачи</span>
    <strong>{issuedAt ? dateOnly(issuedAt) : '—'}</strong>
  </div>

  <div>
    <span>Счёт зачисления</span>
    <strong>{!acceptedOffer || issuedContract.isError
      ? 'Недоступно'
      : issuedContract.isPending
        ? 'Загрузка…'
        : `•••• ${issuedContract.data.borrower.settlement_account.slice(-4)}`}</strong>
  </div>

  <div>
    <span>Ближайший платёж</span>
    <strong>{!acceptedOffer || issuedContract.isError
      ? 'Недоступно'
      : issuedContract.isPending
        ? 'Загрузка…'
        : nextPayment
          ? paymentDate(nextPayment.payment_date)
          : 'Нет предстоящих платежей по графику'}</strong>
    {acceptedOffer && !issuedContract.isError && !issuedContract.isPending && nextPayment && (
      <small className="request-scheduled-payment">{money(nextPayment.payment_rub)}</small>
    )}
  </div>
</div>
                {(!acceptedOffer || issuedContract.isError) && (
                  <div className="request-error" role="alert">
                    Не удалось загрузить данные принятого договора.
                    <button type="button" disabled={issuedContract.isFetching}
                      onClick={() => { void (acceptedOffer ? issuedContract.refetch() : query.refetch()) }}>
                      Повторить
                    </button>
                  </div>
                )}
                <p className="request-demo-note">
                  <Icon name="info" />
                  Демонстрационная выдача. Реальное перечисление средств не
                  выполнялось.
                </p>
              </section>
            )}

            <div className="request-bottom">
              <Disclosure
                title="Параметры кредита"
                expanded={expandedByDefault}
                resetKey={view.label}
              >
                <dl className="request-details">
                  <div>
                    <dt>Компания</dt>
                    <dd>{company?.name ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>ИНН</dt>
                    <dd>{company?.inn ?? '—'}</dd>
                  </div>
                  <div>
                    <dt>
                      {agreedOffer ? 'Кредитный продукт' : 'Продукт заявки'}
                    </dt>
                    <dd>
                      {productName(
                        agreedOffer?.product_code ?? application.product_code,
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>
                      {agreedOffer ? 'Сумма кредита' : 'Запрошенная сумма'}
                    </dt>
                    <dd>{money(agreedOffer?.amount ?? application.amount)}</dd>
                  </div>
                  <div>
                    <dt>{agreedOffer ? 'Срок кредита' : 'Срок заявки'}</dt>
                    <dd>
                      {term(
                        agreedOffer?.term_months ?? application.term_months,
                      )}
                    </dd>
                  </div>
                  {agreedOffer && (
                    <>
                      <div>
                        <dt>Ставка</dt>
                        <dd>{annualRate(agreedOffer.rate)}</dd>
                      </div>
                      <div>
                        <dt>Платёж в месяц</dt>
                        <dd>{money(agreedOffer.monthly_payment)}</dd>
                      </div>
                    </>
                  )}
                  <div>
                    <dt>Цель</dt>
                    <dd>{application.purpose}</dd>
                  </div>
                  {application.purpose_details && (
                    <div>
                      <dt>Ваш комментарий</dt>
                      <dd>{application.purpose_details}</dd>
                    </div>
                  )}
                  {application.investment_object && (
                    <div>
                      <dt>Объект инвестирования</dt>
                      <dd>{application.investment_object}</dd>
                    </div>
                  )}
                  {application.collateral_type && (
                    <div>
                      <dt>Вид залога</dt>
                      <dd>{application.collateral_type}</dd>
                    </div>
                  )}
                  {application.collateral_value !== null && (
                    <div>
                      <dt>Стоимость залога</dt>
                      <dd>{money(application.collateral_value)}</dd>
                    </div>
                  )}
                </dl>
              </Disclosure>
              <Disclosure
                title="История заявки"
                expanded={expandedByDefault}
                resetKey={view.label}
              >
                {history.length ? (
                  <ol
                    className="request-history"
                    aria-label="История заявки: новые события сверху"
                  >
                    {history.map((event, index) => (
                      <li
                        key={event.sequence}
                        className={`request-history-item ${index === 0 ? 'is-latest' : ''}`}
                      >
                        <span
                          className="request-history-dot"
                          aria-hidden="true"
                        />
                        <strong>{event.title}</strong>
                        <time dateTime={event.createdAt}>
                          {dateTime(event.createdAt)}
                        </time>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="request-footnote">Событий пока нет.</p>
                )}
              </Disclosure>
            </div>
          </>
        )}
      </main>
      <dialog
        ref={dialog}
        className="request-dialog"
        aria-labelledby="request-confirm-title"
        onCancel={(event) => {
          event.preventDefault()
          if (!busy) setConfirmation(null)
        }}
      >
        <button
          type="button"
          className="request-dialog-close"
          aria-label="Закрыть"
          disabled={busy}
          onClick={() => setConfirmation(null)}
        >
          <Icon name="close" />
        </button>
        <h2 id="request-confirm-title">{confirmation?.title}</h2>
        <p>{confirmation?.description}</p>
        {error && (
          <div className="request-error" role="alert">
            {error}
          </div>
        )}
        <div className="request-dialog-actions">
          <ButtonDesktop
            type="button"
            view="secondary"
            size={48}
            className="request-outline-button"
            disabled={busy}
            onClick={() => setConfirmation(null)}
          >
            Вернуться
          </ButtonDesktop>
          <ButtonDesktop
            type="button"
            view="accent"
            size={48}
            disabled={busy}
            loading={busyAction === 'confirm'}
            onClick={async () => {
              if (
                confirmation &&
                (await runAction<Application>('confirm', confirmation.path))
              )
                setConfirmation(null)
            }}
          >
            Подтвердить
          </ButtonDesktop>
        </div>
      </dialog>
    </div>
  )
}

function ApplicationPage(props: Props) {
  const { id } = useParams()
  const numericId =
    id && /^[1-9]\d*$/.test(id) && Number(id) <= 2_147_483_647
      ? Number(id)
      : null
  return <ApplicationCard key={id ?? 'invalid'} {...props} id={numericId} />
}

export default ApplicationPage
