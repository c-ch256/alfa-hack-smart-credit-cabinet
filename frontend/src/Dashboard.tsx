import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import type { Session } from './auth'
import './Dashboard.css'
import businessGrowth from './assets/business-growth.png'
import { Link, useNavigate } from 'react-router-dom'
import ClientHeader, { CompanyDetailsDialog } from './ClientHeader'

type Company = {
  id: number
  name: string
  inn: string
  legal_form: string
  okved: string
  industry: string | null
  region: string
  registered_at: string
}

type CreditApplication = {
  id: number
  number: string
  company_id: number
  product_code: string
  amount: number
  status: string
  status_label: string
  created_at: string
}

type Product = {
  code: string
  name: string
}

type DashboardProps = {
  session: Session
  onLogout: () => void
}

async function loadData<T>(path: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
    signal,
  })

  if (response.status === 401) {
    throw new Error('Сессия завершилась. Войдите заново.')
  }

  if (!response.ok) {
    throw new Error('Не удалось загрузить данные. Попробуйте ещё раз.')
  }

  return response.json()
}

function formatDate(value: string) {
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value)

  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('ru-RU')
}

const rubles = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 0,
})

function Dashboard({ session, onLogout }: DashboardProps) {
  const navigate = useNavigate()
  const [companyOpen, setCompanyOpen] = useState(false)
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: ['dashboard', session.user.id],
    queryFn: async ({ signal }) => {
      const [companies, applicationList, products] = await Promise.all([
        loadData<Company[]>('/me/companies', session.token, signal),
        loadData<CreditApplication[]>('/applications?limit=3&offset=0', session.token, signal),
        loadData<Product[]>('/products', session.token, signal),
      ])

      return { companies, applicationList, products }
    },
    staleTime: 30_000,
    retry: false,
  })

  const company = data?.companies[0]

  const companyName = company?.name ?? (isPending ? 'Загрузка…' : session.user.full_name)

  const companyDetails: [string, string][] = company
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
        ['Дата регистрации', formatDate(company.registered_at)],
      ]
    : []

  const applications = (data?.applicationList ?? []).map((item) => ({
    id: item.id,
    number: item.number,
    href: item.status === 'draft' ? `/applications/${item.id}/edit` : `/applications/${item.id}`,
    product: data?.products.find((product) => product.code === item.product_code)?.name ?? 'Кредит',
    amount: rubles.format(item.amount),
    status:
      (
        {
          draft: 'Черновик',
          submitted: 'На рассмотрении',
          checking: 'На рассмотрении',
          scoring: 'На рассмотрении',
          decision: 'На рассмотрении',
          manual_review: 'На рассмотрении',
          approved: 'Одобрено',
          counter_offer: 'Новое предложение',
          needs_info: 'Нужны сведения',
          accepted: 'Ожидает подписания',
          signed_demo: 'Оформляется выдача',
          disbursed_demo: 'Кредит выдан (демо)',
          rejected: 'Отказано',
          closed: 'Закрыта',
        } as Record<string, string>
      )[item.status] ?? 'На рассмотрении',
    date: formatDate(item.created_at),
  }))

  return (
    <div className="dashboard">
      <ClientHeader
        session={session}
        onLogout={onLogout}
        company={company ?? null}
        loading={isPending}
      />
      <CompanyDetailsDialog
        session={session}
        company={company ?? null}
        open={companyOpen}
        onClose={() => setCompanyOpen(false)}
      />

      <main className="dashboard-main">
        {isPending && <p role="status">Загружаем данные кабинета…</p>}

        {isError && (
          <div role="alert">
            <p>{error instanceof TypeError ? 'Нет соединения с сервером.' : error.message}</p>

            <button type="button" onClick={() => void refetch()}>
              Повторить
            </button>

            <button type="button" onClick={onLogout}>
              Вернуться ко входу
            </button>
          </div>
        )}
        <div className="dashboard-heading">
          <p>Добро пожаловать!</p>
          <h1>{companyName}</h1>
          <span>Ваш кредитный кабинет для бизнеса</span>
        </div>

        <div className="dashboard-grid">
          <section className="dashboard-card">
            <div className="dashboard-card-heading">
              <h2>
                <button
                  type="button"
                  className="dashboard-company-title"
                  disabled={!company}
                  aria-haspopup="dialog"
                  onClick={() => setCompanyOpen(true)}
                >
                  Моя компания
                </button>
              </h2>
              <span className="neutral-badge">Данные из банка</span>
            </div>

            <dl className="company-details">
              {data && !company && (
                <div className="company-detail">
                  <dt>Компания</dt>
                  <dd>К вашему профилю пока не привязана компания.</dd>
                </div>
              )}

              {companyDetails.map(([label, value]) => (
                <div className="company-detail" key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>

            <button
              type="button"
              className="dashboard-text-button"
              disabled={!company}
              onClick={() => setCompanyOpen(true)}
            >
              Данные компании
              <span aria-hidden="true">→</span>
            </button>
          </section>

          <section className="dashboard-card">
            <div className="dashboard-card-heading">
              <h2><Link to="/applications" className="dashboard-applications-title">Мои заявки</Link></h2>
              <button
                type="button"
                className="dashboard-text-button"
                onClick={() => navigate('/applications')}
              >
                Все заявки
                <span aria-hidden="true">→</span>
              </button>
            </div>

            <div className="applications-table-wrapper">
              <table className="applications-table">
                <caption className="visually-hidden">Последние кредитные заявки</caption>
                <thead>
                  <tr>
                    <th scope="col">Заявка</th>
                    <th scope="col">Сумма</th>
                    <th scope="col">Статус</th>
                    <th scope="col">Дата</th>
                  </tr>
                </thead>
                <tbody>
                  {data && applications.length === 0 && (
                    <tr>
                      <td colSpan={4}>У вас пока нет заявок.</td>
                    </tr>
                  )}
                  {applications.map((application) => (
                    <tr
                      key={application.id}
                      className="dashboard-application-row"
                      tabIndex={0}
                      onClick={() => navigate(application.href)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && event.target === event.currentTarget) {
                          event.preventDefault()
                          navigate(application.href)
                        }
                      }}
                    >
                      <td>
                        <Link
                          to={application.href}
                          className="application-product dashboard-application-link"
                          onClick={(event) => event.stopPropagation()}
                        >
                          {application.product}
                        </Link>
                        <span className="application-number">{application.number}</span>
                      </td>
                      <td className="application-amount">{application.amount}</td>
                      <td>
                        <span className="neutral-badge">{application.status}</span>
                      </td>
                      <td className="application-date">{application.date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
        <section className="growth-banner" aria-labelledby="growth-title">
          <div className="growth-content">
            <h2 id="growth-title">Возможности для роста вашего бизнеса</h2>

            <p>Рассчитайте условия, отправьте заявку и следите за решением онлайн.</p>

            <ButtonDesktop
              view="accent"
              size={48}
              type="button"
              onClick={() => navigate('/credits')}
            >
              Подобрать кредит
            </ButtonDesktop>

            <ol className="growth-steps">
              <li>
                <span aria-hidden="true">1</span>
                Расчёт условий
              </li>
              <li>
                <span aria-hidden="true">2</span>
                Решение банка
              </li>
              <li>
                <span aria-hidden="true">3</span>
                Подписание и выдача
              </li>
            </ol>
          </div>

          <img className="growth-image" src={businessGrowth} alt="" />
        </section>
      </main>
    </div>
  )
}

export default Dashboard
