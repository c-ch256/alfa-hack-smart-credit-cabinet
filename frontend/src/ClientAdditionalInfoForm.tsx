import { useState } from 'react'
import type { FormEvent } from 'react'
import { ButtonDesktop } from '@alfalab/core-components/button/desktop'
import { additionalInfoFields, infoPayload, initialInfoValues, requestedInfoFields } from './clientAdditionalInfo'
import type { InfoApplication, InfoField, InfoPayload } from './clientAdditionalInfo'

function MoneyField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <label className="request-field"><span>Оценочная стоимость залога</span>
    <input type="text" inputMode="numeric" autoComplete="off" required maxLength={24}
      value={value === '' ? '' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(Number(value))}
      onChange={(event) => {
        const input = event.currentTarget
        const digitsBefore = input.value.slice(0, input.selectionStart ?? input.value.length).replace(/\D/g, '').length
        onChange(input.value.replace(/\D/g, ''))
        requestAnimationFrame(() => {
          if (document.activeElement !== input) return
          let position = 0, digits = 0
          while (position < input.value.length && digits < digitsBefore) {
            if (/\d/.test(input.value[position])) digits++
            position++
          }
          input.setSelectionRange(position, position)
        })
      }} />
  </label>
}

export default function ClientAdditionalInfoForm({ application, busy, loading, onSubmit, onWithdraw }: {
  application: InfoApplication
  busy: boolean
  loading: boolean
  onSubmit: (payload: InfoPayload) => Promise<boolean>
  onWithdraw: () => void
}) {
  const [values, setValues] = useState(initialInfoValues)
  const [error, setError] = useState('')
  const fields = requestedInfoFields(application)
  function update(field: InfoField, value: string) {
    setValues((previous) => ({ ...previous, [field]: value }))
    setError('')
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    try { await onSubmit(infoPayload(application, values)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Проверьте заполненные сведения.') }
  }
  function fieldControl(field: InfoField) {
    if (field === 'collateral_value') return <MoneyField key={field} value={values[field]} onChange={(value) => update(field, value)} />
    return <label key={field} className="request-field"><span>{additionalInfoFields[field].label}</span>
      {field === 'purpose_details' ? <>
        <textarea required maxLength={additionalInfoFields[field].maxLength} value={values[field]} rows={3}
          onChange={(event) => update(field, event.target.value)} />
        <small className="request-character-count">{values[field].length} / {additionalInfoFields[field].maxLength}</small>
      </> : <input required maxLength={additionalInfoFields[field].maxLength} value={values[field]}
        onChange={(event) => update(field, event.target.value)} />}
    </label>
  }
  const pledgeFields = fields?.filter((field) => field === 'collateral_type' || field === 'collateral_value') ?? []
  return <form className="request-information-form" onSubmit={submit}>
    <fieldset disabled={busy}>
      {!fields ? <p className="request-footnote" role="status">Запрошенные сведения недоступны. Обновите страницу.</p> : <>
        {fields.filter((field) => field === 'purpose_details' || field === 'investment_object').map(fieldControl)}
        {pledgeFields.length > 0 && <div className={pledgeFields.length > 1 ? 'request-two-fields' : undefined}>
          {pledgeFields.map(fieldControl)}
        </div>}
      </>}
    </fieldset>
    {error && <p className="request-error" role="alert">{error}</p>}
    <div className="request-actions">
      <ButtonDesktop type="submit" view="accent" size={48} disabled={busy || !fields} loading={loading}>Отправить сведения</ButtonDesktop>
      <ButtonDesktop type="button" view="secondary" size={48} className="request-outline-button" disabled={busy} onClick={onWithdraw}>Отозвать заявку</ButtonDesktop>
    </div>
  </form>
}
