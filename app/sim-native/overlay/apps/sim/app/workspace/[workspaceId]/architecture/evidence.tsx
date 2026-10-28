'use client'

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value)
  ? value as Record<string, unknown> : {}
const rows = (value: unknown) => Array.isArray(value) ? value.map(object) : []
const text = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value) : 'UNKNOWN'

export function ArchitectureEvidence({ value, kind, proposed }: { value: unknown; kind: string; proposed: boolean }) {
  const record = object(value)
  if (!Object.keys(record).length) return <p>No evidence loaded.</p>
  if (record.status === 'UNKNOWN') return <p>{text(record.reason)}</p>
  if (record.kind !== (kind === 'Plan' ? 'minusops-review-graph' : 'minusops-canvas-delivery')) return <p>No matching evidence loaded.</p>
  const views = object(record.views)
  const delivery = object(views.delivery)
  const workflow = object(views.workflow)
  const deployment = object(views.deployment)
  const helm = object(deployment.helm)
  const provenance = object(record.provenance)
  return <>
    {proposed && <p className='mb-3 text-xs text-[var(--text-tertiary)]'>Published source evidence. Pending changes require a new plan and review.</p>}
    {kind === 'Plan' ? <>
      <p className='mb-2'>{rows(record.nodes).length} resources in the qualified saved plan.</p>
      <dl className='mb-3 space-y-1 text-xs'><div><dt>Plan hash</dt><dd className='break-all'>{text(provenance.review_hash)}</dd></div>
        <div><dt>Cost / runtime / apply eligibility</dt><dd>UNKNOWN</dd></div></dl>
      <div className='max-h-64 overflow-auto'><table className='w-full text-left text-xs'><thead><tr><th className='py-1'>Resource</th><th>Change</th></tr></thead>
        <tbody>{rows(record.nodes).map(node => <tr key={text(node.id)}><td className='break-all py-1 pr-3'>{text(node.address)}</td>
          <td>{Array.isArray(node.actions) ? node.actions.map(text).join(', ') : 'UNKNOWN'}</td></tr>)}</tbody></table></div>
    </> : <>
      <dl className='grid grid-cols-2 gap-2 text-xs'><dt>Delivery route</dt><dd>{text(delivery.route)}</dd>
        <dt>Stage mapping</dt><dd>{text(delivery.stage_mapping)}</dd><dt>Workflow execution</dt><dd>{text(workflow.execution_observed)}</dd>
        <dt>Controller health</dt><dd>{text(deployment.controller_health)}</dd></dl>
      {Object.entries({ 'CI sources': delivery.sources, 'Helm charts': helm.charts, 'Helm values': helm.values,
        'Deployment manifests': helm.manifests, 'Argo CD sources': deployment.argo_sources }).map(([label, sources]) => <details key={label} className='mt-3 text-xs'>
        <summary className='cursor-pointer'>{label} · {rows(sources).length}</summary>
        {rows(sources).length ? <ul className='mt-2 space-y-1'>{rows(sources).map(source => <li key={text(source.path)} title={text(source.sha256)}
          className='break-all'>{text(source.path)}{typeof source.version === 'string' ? ` · ${source.version}` : ''}</li>)}</ul>
          : <p className='mt-2 text-[var(--text-tertiary)]'>No admitted run source. Applicability remains unconfirmed.</p>}
      </details>)}
      <details className='mt-3 text-xs'><summary className='cursor-pointer'>Application owner handoff</summary>
        <pre className='mt-2 whitespace-pre-wrap break-words'>{JSON.stringify(delivery.minusflow ?? { status: 'UNKNOWN' }, null, 2)}</pre></details>
    </>}
    <details className='mt-4'><summary className='cursor-pointer'>Evidence</summary>
      <pre className='mt-3 whitespace-pre-wrap break-words text-xs'>{JSON.stringify(record, null, 2)}</pre></details>
  </>
}
