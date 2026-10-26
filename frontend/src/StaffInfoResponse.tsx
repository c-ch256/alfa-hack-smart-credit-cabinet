import { dateLabel } from './staffApplicationDetail'
import { infoFieldNames, infoValue } from './staffAdditionalInfo'
import type { SavedInfoResponse } from './staffAdditionalInfo'
import './StaffInfoResponse.css'

export default function StaffInfoResponse({ response }: { response: SavedInfoResponse }) {
  return <div className="staff-info-response">
    <p className="staff-info-answer-date">Дата ответа: <time dateTime={response.answered_at}>{dateLabel(response.answered_at)}</time></p>
    {response.requested_fields && response.requested_fields.length > 0 && <>
      <p className="staff-info-requested-label">Запрошенные сведения</p>
      <ul>{response.requested_fields.map((field) => <li key={field}>{infoFieldNames[field]}</li>)}</ul>
    </>}
    {response.changes !== null && response.changes.length > 0 && <div className="staff-info-table-scroll" tabIndex={0} role="region" aria-label="Изменения сведений клиента">
      <table className="staff-info-table"><thead><tr><th scope="col">Поле</th><th scope="col">Было</th><th scope="col">Стало</th></tr></thead>
        <tbody>{response.changes.map((change, index) => <tr key={`${change.field}-${index}`}>
          <th scope="row">{infoFieldNames[change.field]}</th><td>{infoValue(change.field, change.before)}</td><td>{infoValue(change.field, change.after)}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {response.changes === null ? <p className="staff-info-answer-date">Состав изменений не сохранён</p>
      : !response.changes.length && <p className="staff-info-answer-date">Значения полей не изменились.</p>}
  </div>
}
