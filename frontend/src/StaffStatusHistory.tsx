import { actorLabel, dateLabel, newestHistory, statusLabel } from './staffApplicationDetail'
import type { StaffEvent } from './staffApplicationDetail'
import type { Session } from './auth'
import './StaffStatusHistory.css'

export default function StaffStatusHistory({ history, compact = false, user, companyName }: { history: StaffEvent[]; compact?: boolean; user?: Session['user']; companyName?: string | null }) {
  return <ol className={`staff-detail-history${compact ? ' staff-status-history-compact' : ''}`}>
    {newestHistory(history).map((event, index) => <li key={event.sequence} className={index === 0 ? 'is-latest' : undefined}>
      <span className="staff-detail-history-dot" aria-hidden="true" />
      <strong>{!compact && event.from_status ? `${statusLabel(event.from_status)} → ` : ''}{statusLabel(event.to_status)}</strong>
      <time dateTime={event.created_at}>{dateLabel(event.created_at)}</time>
      {!compact && <><span>{actorLabel(event, user, companyName)}</span>{event.reason && <p>{event.reason}</p>}</>}
    </li>)}
  </ol>
}
