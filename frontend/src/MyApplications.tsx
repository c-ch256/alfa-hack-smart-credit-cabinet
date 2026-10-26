import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import type { Session } from './auth'
import ClientHeader from './ClientHeader'
import type { CabinetCompany } from './ClientHeader'
import './Dashboard.css'
import './CreditCalculator.css'
import './MyApplications.css'

type Application = {
  id: number
  number: string
  product_code: string
  amount: number
  status: string
  created_at: string
}

type Company = CabinetCompany

type Product = {
  code: string
  name: string
}

type Props = {
  session: Session
  onLogout: () => void
}

type Filter = 'all' | 'active' | 'drafts' | 'completed'

const filters: { value: Filter; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'active', label: 'В работе' },
  { value: 'drafts', label: 'Черновики' },
  { value: 'completed', label: 'Завершённые' },
]

const completedStatuses = new Set([
  'rejected',
  'closed',
  'disbursed_demo',
])

const statusLabels: Record<string, string> = {
  draft: 'Черновик',
  submitted: 'На рассмотрении',
  checking: 'На рассмотрении',
  scoring: 'На рассмотрении',
  decision: 'На рассмотрении',
  manual_review: 'На рассмотрении',
  needs_info: 'Нужны сведения',
  approved: 'Одобрено',
  counter_offer: 'Новое предложение',
  accepted: 'Подписание',
  signed_demo: 'Подписано (демо)',
  disbursed_demo: 'Кредит выдан (демо)',
  rejected: 'Отказано',
  closed: 'Закрыта',
}

const money = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 0,
})

async function load<T>(
  path: string,
  token: string,
  signal: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  })

  if (response.status === 401) {
    throw new Error('Сессия завершена. Выйдите и войдите снова.')
  }

  if (!response.ok) {
    throw new Error('Не удалось загрузить данные. Попробуйте ещё раз.')
  }

  return response.json() as Promise<T>
}

async function loadApplications(
  token: string,
  signal: AbortSignal,
) {
  const applications: Application[] = []

  for (let offset = 0; ; offset += 100) {
    const page = await load<Application[]>(
      `/applications?limit=100&offset=${offset}`,
      token,
      signal,
    )

    applications.push(...page)

    if (page.length < 100) break
  }

  return [...new Map(
    applications.map((application) => [
      application.id,
      application,
    ]),
  ).values()]
}

function MyApplications({ session, onLogout }: Props) {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<Filter>('all')
  const [newestFirst, setNewestFirst] = useState(true)

  const query = useQuery({
    queryKey: ['my-applications', session.user.id],
    queryFn: async ({ signal }) => {
      const [applications, companies, products] = await Promise.all([
        loadApplications(session.token, signal),
        load<Company[]>('/me/companies', session.token, signal),
        load<Product[]>('/products', session.token, signal),
      ])

      return { applications, companies, products }
    },
    staleTime: 0,
    retry: false,
  })

  const data = query.data
  const applications = (data?.applications ?? [])
    .filter((application) => {
      if (filter === 'drafts') {
        return application.status === 'draft'
      }

      if (filter === 'completed') {
        return completedStatuses.has(application.status)
      }

      if (filter === 'active') {
        return (
          application.status !== 'draft' &&
          !completedStatuses.has(application.status)
        )
      }

      return true
    })
    .sort((a, b) => {
      const difference =
        Date.parse(b.created_at) - Date.parse(a.created_at) ||
        b.id - a.id

      return newestFirst ? difference : -difference
    })

  return (
    <div className="dashboard my-applications-page">
      <ClientHeader
        session={session}
        onLogout={onLogout}
        company={data?.companies[0] ?? null}
        loading={query.isPending}
      />

      <main className="my-applications-main">
        <div className="my-applications-heading">
          <h1>Мои заявки</h1>
          <p>
            Здесь вы можете следить за статусом кредитных заявок
            и продолжить оформление
          </p>
        </div>

        <section
          className="my-applications-panel"
          aria-label="Кредитные заявки"
        >
          <div className="my-applications-toolbar">
            <div
              className="my-applications-filters"
              role="group"
              aria-label="Фильтр заявок"
            >
              {filters.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={filter === item.value ? 'is-selected' : ''}
                  aria-pressed={filter === item.value}
                  onClick={() => setFilter(item.value)}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <ButtonDesktop
              type="button"
              view="accent"
              size={48}
              onClick={() => navigate('/credits')}
            >
              Новая заявка
            </ButtonDesktop>
          </div>

          {query.isPending ? (
            <div className="my-applications-state" role="status">
              Загружаем заявки…
            </div>
          ) : query.isError ? (
            <div className="my-applications-state" role="alert">
              <h2>Не удалось загрузить заявки</h2>
              <p>{query.error.message}</p>
              <ButtonDesktop
                type="button"
                view="secondary"
                size={48}
                onClick={() => void query.refetch()}
              >
                Повторить
              </ButtonDesktop>
            </div>
          ) : applications.length === 0 ? (
            <div className="my-applications-state">
              <h2>
                {data?.applications.length
                  ? 'В этой вкладке пока нет заявок'
                  : 'У вас пока нет заявок'}
              </h2>
              <p>
                {data?.applications.length
                  ? 'Выберите другую вкладку.'
                  : 'Подберите кредит и сохраните первый черновик.'}
              </p>
            </div>
          ) : (
            <div className="my-applications-scroll">
              <table className="my-applications-table">
                <caption className="visually-hidden">
                  Ваши кредитные заявки
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Заявка</th>
                    <th scope="col">Сумма</th>
                    <th scope="col">Статус</th>
                    <th
                      scope="col"
                      aria-sort={newestFirst ? 'descending' : 'ascending'}
                    >
                      <button
                        type="button"
                        className="my-applications-sort"
                        onClick={() => setNewestFirst((value) => !value)}
                      >
                        Дата создания
                        <span aria-hidden="true">
                          {newestFirst ? '↓' : '↑'}
                        </span>
                      </button>
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {applications.map((application) => (
                    <tr
  key={application.id}
  className="is-clickable"
  tabIndex={0}
  onClick={() => {
    navigate(
      application.status === 'draft'
        ? `/applications/${application.id}/edit`
        : `/applications/${application.id}`,
    )
  }}
  onKeyDown={(event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()

      navigate(
        application.status === 'draft'
          ? `/applications/${application.id}/edit`
          : `/applications/${application.id}`,
      )
    }
  }}
>
  <td>
    <span className="my-application-product">
      {data?.products.find(
        (product) => product.code === application.product_code,
      )?.name ??
        (application.product_code === 'working_capital'
          ? 'Оборотный кредит'
          : application.product_code === 'investment'
            ? 'Инвестиционный кредит'
            : 'Кредит')}
    </span>

    <span className="my-application-number">
      {application.number}
    </span>
  </td>

  <td className="my-application-amount">
    {money.format(application.amount)}
  </td>

  <td>
    <span className="neutral-badge">
      {statusLabels[application.status] ?? 'На рассмотрении'}
    </span>
  </td>

  <td className="my-application-date">
    {new Date(application.created_at).toLocaleDateString('ru-RU')}
  </td>
</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

export default MyApplications