'use client'

import { ChipInput, ChipSwitch, ChipTextarea } from '@sim/emcn'
import type { SourceNode } from '@/lib/api/contracts/minusops-architecture'

type Field = SourceNode['fields'][string]

export function ConfigurationField({ name, field, text, disabled, onChange }: {
  name: string; field: Field; text?: string; disabled: boolean; onChange: (text: string) => void
}) {
  const value = text ?? (typeof field.value === 'string' ? field.value : JSON.stringify(field.value, null, 2) ?? '')
  const readOnly = disabled || !field.editable
  return <div className='mb-3 min-w-0'>
    <label htmlFor={`field-${name}`} className='mb-1 block text-xs'>{name}</label>
    {field.kind === 'literal' && typeof field.value === 'boolean'
      ? <ChipSwitch aria-label={name} value={value} options={[{ value: 'true', label: 'True' }, { value: 'false', label: 'False' }]}
          disabled={readOnly} onChange={onChange} />
      : typeof field.value === 'string' || typeof field.value === 'number'
        ? <ChipInput id={`field-${name}`} aria-label={name} value={value} readOnly={readOnly}
            type={typeof field.value === 'number' ? 'number' : 'text'}
            onChange={event => onChange(event.target.value)} />
        : <ChipTextarea id={`field-${name}`} aria-label={name} value={value} readOnly={readOnly}
            onChange={event => onChange(event.target.value)} />}
    {(field.kind !== 'literal' || !field.editable) && <p className='mt-1 text-xs text-[var(--text-tertiary)]'>
      {field.overridden_by ? 'Overridden by module input' : field.kind === 'reference' && field.editable ? 'Declared reference' : 'Inspect only'}
    </p>}
    {field.type_evidence && <p className='mt-1 break-words text-xs text-[var(--text-tertiary)]'>
      {field.type_evidence.type} · {field.type_evidence.source.path}:{field.type_evidence.source.start_line}
    </p>}
  </div>
}
