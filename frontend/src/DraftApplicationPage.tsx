import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import CreditCalculator from './CreditCalculator'
import type { DraftApplication } from './CreditCalculator'
import type { Session } from './auth'

type Props = {
  session: Session
  onLogout: () => void
}

function DraftApplicationPage({ session, onLogout }: Props) {
  const { id } = useParams()

  const validId = Boolean(
    id &&
    /^[1-9]\d*$/.test(id) &&
    Number(id) <= 2_147_483_647,
  )

  const query = useQuery({
    queryKey: ['draft-application', session.user.id, id],
    enabled: validId,
    queryFn: async ({ signal }) => {
      const response = await fetch(
        `/api/applications/${Number(id)}`,
        {
          headers: {
            Authorization: `Bearer ${session.token}`,
          },
          signal,
        },
      ).catch((cause: unknown) => {
        if (signal.aborted) throw cause
        throw new Error(
          'Нет соединения с сервером. Попробуйте ещё раз.',
        )
      })

      if (response.status === 401) {
        throw new Error(
          'Сессия завершена. Выйдите и войдите снова.',
        )
      }

      if (response.status === 404) {
        throw new Error('Черновик не найден или недоступен.')
      }

      if (!response.ok) {
        throw new Error('Не удалось загрузить черновик.')
      }

      const draft = await response.json() as DraftApplication

      if (draft.status !== 'draft') {
        throw new Error(
          'Эта заявка уже отправлена. Продолжить её как черновик нельзя.',
        )
      }

      return draft
    },
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  })

  if (validId && query.data) {
    return (
      <CreditCalculator
        session={session}
        onLogout={onLogout}
        initialDraft={query.data}
      />
    )
  }

  return (
    <div className="dashboard credit-page">
      <main className="credit-main">
        <Link to="/applications" className="credit-back">
          ← К списку заявок
        </Link>

        {!validId || query.isError ? (
          <div className="credit-notice" role="alert">
            <p>
              {!validId
                ? 'Некорректный номер черновика.'
                : query.error?.message}
            </p>

            {validId && (
              <button
                type="button"
                onClick={() => void query.refetch()}
              >
                Повторить
              </button>
            )}
          </div>
        ) : (
          <p role="status">Открываем черновик…</p>
        )}
      </main>
    </div>
  )
}

export default DraftApplicationPage