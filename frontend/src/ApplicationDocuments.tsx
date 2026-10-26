import { useEffect, useId, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { contractQueryOptions, DocumentError, fetchDocument, loadFixedSchedule, paymentDate } from './creditDocuments'
import type { Payment } from './creditDocuments'
import './ApplicationDocuments.css'

type DocumentKind = 'contract' | 'schedule'
type DocumentState =
  | { status: 'idle' | 'loading' }
  | { status: 'error'; message: string }
  | { status: 'pdf'; url: string }
  | { status: 'schedule'; payments: Payment[] }

const titles: Record<DocumentKind, string> = {
  contract: 'Кредитный договор',
  schedule: 'График платежей',
}
const rubles = new Intl.NumberFormat('ru-RU', {
  style: 'currency', currency: 'RUB', maximumFractionDigits: 0,
})

function DocumentIcon() {
  return <svg width="21" height="21" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true">
    <path d="M14 3H5v18h14V8l-5-5ZM14 3v5h5M8 12h8M8 16h5" />
  </svg>
}

export default function ApplicationDocuments({ offerId, applicationId, token, userId, revision }: {
  offerId: number
  applicationId: number
  token: string
  userId: number
  revision: string
}) {
  const client = useQueryClient()
  const [selection, setSelection] = useState<{ kind: DocumentKind; attempt: number } | null>(null)
  const [document, setDocument] = useState<DocumentState>({ status: 'idle' })
  const [errors, setErrors] = useState<Partial<Record<DocumentKind, string>>>({})
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (selection && !element.open) element.showModal()
  }, [selection])

  useEffect(() => {
    if (!selection) return
    const kind = selection.kind
    const controller = new AbortController()
    const { signal } = controller
    let objectUrl: string | undefined
    const base = `/offers/${offerId}`

    async function load() {
      try {
        if (kind === 'contract') {
          // Общий с экраном выдачи запрос: снимок, а не текущий профиль компании.
          await client.fetchQuery(contractQueryOptions({ offerId, applicationId, userId, token, revision }))
          if (signal.aborted) return
          const response = await fetchDocument(`${base}/contract.pdf`, token, signal)
          const blob = await response.blob()
          if (!blob.size || blob.type.split(';')[0] !== 'application/pdf') {
            throw new DocumentError('Сервер не вернул PDF договора. Попробуйте ещё раз.')
          }
          if (signal.aborted) return
          objectUrl = URL.createObjectURL(blob)
          setDocument({ status: 'pdf', url: objectUrl })
        } else {
          const payments = await loadFixedSchedule(offerId, token, signal)
          if (!signal.aborted) setDocument({ status: 'schedule', payments })
        }
      } catch (cause) {
        if (signal.aborted) return
        // Только собственные тексты: SyntaxError/TypeError могут содержать ответ сервера.
        const message = cause instanceof DocumentError
          ? cause.message
          : 'Не удалось загрузить документ. Проверьте соединение и повторите запрос.'
        setDocument({ status: 'error', message })
        setErrors((previous) => ({ ...previous, [kind]: message }))
      }
    }
    void load()
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [selection, offerId, applicationId, token, userId, revision, client])

  function open(kind: DocumentKind) {
    setDocument({ status: 'loading' })
    setErrors((previous) => ({ ...previous, [kind]: undefined }))
    setSelection((previous) => ({ kind, attempt: (previous?.attempt ?? 0) + 1 }))
  }

  function close() {
    setSelection(null)
    setDocument({ status: 'idle' })
  }

  return <div className="application-documents">
    <p className="application-documents-notice">
      Демонстрационный договор. Не является юридически значимым документом.
    </p>
    {(['contract', 'schedule'] as const).map((kind) => <div key={kind} className="application-document-item">
      <div className="application-document-row">
        <span className="application-document-name"><DocumentIcon />{titles[kind]}</span>
        <button type="button" className="application-document-button" onClick={() => open(kind)}
          aria-label={`Просмотреть: ${titles[kind]}`} aria-haspopup="dialog">
          Просмотреть
        </button>
      </div>
      {selection?.kind === kind && document.status === 'loading' &&
        <p className="application-document-status" role="status">Загружаем документ…</p>}
      {errors[kind] && <div className="application-document-error" role="alert">
        <span>{errors[kind]}</span>
        <button type="button" className="request-text-button" onClick={() => open(kind)}>Повторить</button>
      </div>}
    </div>)}
    {selection && <dialog ref={dialog} className="application-document-dialog" aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); close() }} onClose={close}>
      <div className="application-document-heading">
        <h2 id={titleId}>{selection ? titles[selection.kind] : 'Документы'}</h2>
        <button type="button" className="application-document-close" onClick={close}
          aria-label="Закрыть документ" autoFocus>×</button>
      </div>
      <p className="application-documents-notice">
        Демонстрационный договор. Не является юридически значимым документом.
      </p>
      {document.status === 'loading' && <p role="status">Загружаем документ…</p>}
      {document.status === 'error' && <div className="application-document-error" role="alert">
        <span>{document.message}</span>
        <button type="button" className="application-document-button"
          onClick={() => selection && open(selection.kind)}>Повторить</button>
      </div>}
      {document.status === 'pdf' && <>
        <object className="application-document-pdf" data={document.url} type="application/pdf"
          aria-label="PDF демонстрационного кредитного договора">
          <p>Браузер не отображает PDF. <a href={document.url} download={`DEMO-${String(offerId).padStart(8, '0')}.pdf`}>
            Скачать договор и график
          </a></p>
        </object>
        <div className="application-document-download">
          <span>Если PDF не отображается, скачайте файл.</span>
          <a className="application-document-button" href={document.url}
            download={`DEMO-${String(offerId).padStart(8, '0')}.pdf`}>Скачать договор и график</a>
        </div>
      </>}
      {document.status === 'schedule' && <div className="application-schedule-scroll" tabIndex={0}
        role="region" aria-label="Таблица графика платежей">
        <table className="application-schedule-table">
          <caption className="visually-hidden">Плановый график платежей по принятому предложению</caption>
          <thead><tr>{['№', 'Дата', 'Платёж', 'Проценты', 'Основной долг', 'Остаток'].map((label) =>
            <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{document.payments.map((row) => <tr key={row.number}>
            <th scope="row">{row.number}</th><td>{paymentDate(row.payment_date)}</td>
            <td>{rubles.format(row.payment_rub)}</td><td>{rubles.format(row.interest_rub)}</td>
            <td>{rubles.format(row.principal_rub)}</td><td>{rubles.format(row.balance_rub)}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </dialog>}
  </div>
}
