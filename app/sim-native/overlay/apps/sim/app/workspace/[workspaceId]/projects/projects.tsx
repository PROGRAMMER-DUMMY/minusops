'use client'

import { type FormEvent, useId, useRef, useState } from 'react'
import Link from 'next/link'
import { Button, Input, DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@sim/emcn'
import { ChevronDown } from '@sim/emcn/icons'
import { useSession } from '@/lib/auth/client'
import { useFeatureFlag } from '@/app/workspace/[workspaceId]/providers/feature-flags-provider'
import { useUserPermissionsContext } from '@/app/workspace/[workspaceId]/providers/workspace-permissions-provider'
import { useProjectCatalog, useProjectConnections, useProjectSetup } from '@/hooks/queries/enterprise-chat'
import type { ProjectSetup, ProjectSetupAction } from '@/lib/api/contracts/project-setup'
import { projectReaderForCredential } from '@/lib/project-integrations'
import { useOpenRouterChatStore } from '@/stores/openrouter-chat/store'
import { useOpenRouterChat } from '@/hooks/queries/openrouter-chat'

const fieldClass = 'w-full rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text-primary)]'
const labels: Record<string, string> = {CONNECTION_REQUIRED: 'Connection required',
  SOURCES_REQUIRED: 'Sources required', ACCESS_REQUIRED: 'Access setup pending', ADMITTED: 'Access configured'}

function ProjectForm({workspaceId, userId, project, mode, canAdmin, onSaved}: {
  workspaceId: string; userId?: string; project?: ProjectSetup; mode: 'create' | 'import'
  canAdmin: boolean; onSaved: (id: string) => void
}) {
  const fieldId = useId()
  const [name, setName] = useState(project?.name ?? '')
  const [description, setDescription] = useState(project?.description ?? '')
  const [urls, setUrls] = useState(project?.source_urls.join('\n') ?? '')
  const [credentialId, setCredentialId] = useState<string | null>(project?.credential_id ?? null)
  const model = useOpenRouterChatStore(state => state.workspaces[workspaceId]?.preferences.model)
  const [chatModel, setChatModel] = useState<string | null>(project?.chat_model ?? null)
  const [error, setError] = useState('')
  const pending = useRef<{payload: string; id: string} | null>(null)
  const {update} = useProjectSetup(workspaceId, userId)
  const connections = useProjectConnections(workspaceId, userId, canAdmin)
  const data = connections.data
  const accounts = !connections.isError && !connections.isFetching && data && 'credentials' in data
    ? data.credentials : []
  const selected = accounts.find(account => account.id === credentialId)
  const disabled = !canAdmin || update.isPending

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const sources = urls.split(/\r?\n/).map(value => value.trim()).filter(Boolean)
    try {
      const sites = new Set<string>()
      for (const source of sources) {
        const parsed = new URL(source)
        if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.search || parsed.hash
            || !/^\/(?:wiki\/)?(?:spaces\/[A-Za-z0-9_~.-]+\/)?pages\/[1-9][0-9]*(?:\/[^/?#]*)?\/?$/.test(parsed.pathname)) {
          throw new Error('invalid')
        }
        sites.add(parsed.origin)
      }
      if (sources.length > 8 || sites.size > 1 || (mode === 'import' && !project && !sources.length)) throw new Error('invalid')
    } catch {
      setError('Use up to eight full Confluence page links from one site, without query strings or fragments.')
      return
    }
    const values = {name: name.trim(), description: description.trim(), source_urls: sources}
    let body: ProjectSetupAction
    if (project) body = {operation: 'save', ...values, project_id: project.project_id,
      revision: project.revision, credential_id: credentialId,
      chat_model: chatModel === model && credentialId && sources.length && model ? model : null}
    else {
      const payload = JSON.stringify({mode, ...values})
      const request = pending.current?.payload === payload ? pending.current : {payload, id: crypto.randomUUID()}
      pending.current = request
      body = {operation: mode, ...values, request_id: request.id}
    }
    try {
      const result = await update.mutateAsync(body)
      if (result.updated_project_id) onSaved(result.updated_project_id)
    } catch {
      setError('The project could not be saved. Check the connection and your access, or refresh if the project changed in another tab.')
    }
  }

  return <form onSubmit={save} className='flex max-w-2xl flex-col gap-5' aria-label={project ? 'Project setup' : mode === 'import' ? 'Import project' : 'Create project'}>
    <div className='flex flex-col gap-2'>
      <label htmlFor={`${fieldId}-name`} className='text-sm'>Project name</label>
      <Input id={`${fieldId}-name`} value={name} onChange={event => setName(event.target.value)}
        required maxLength={160} disabled={disabled} placeholder='Name your project' />
    </div>
    <div className='flex flex-col gap-2'>
      <label htmlFor={`${fieldId}-description`} className='text-sm'>Description</label>
      <textarea id={`${fieldId}-description`} className={fieldClass} rows={3} maxLength={2000}
        value={description} onChange={event => setDescription(event.target.value)} disabled={disabled}
        placeholder='What should your team understand about this project?' />
    </div>
    {project && <div className='flex flex-col items-start gap-2'>
      <span className='text-sm' id={`${fieldId}-account`}>Integration account</span>
      <DropdownMenu onOpenChange={open => {if (open) void connections.refetch()}}>
        <DropdownMenuTrigger asChild>
          <Button type='button' variant='default' disabled={disabled} aria-labelledby={`${fieldId}-account`}>
            {connections.isFetching ? 'Checking accounts...' : selected?.displayName
              ?? (credentialId ? 'Account unavailable' : 'Connect later')}
            <ChevronDown className='ml-2 size-3' aria-hidden='true' />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align='start'>
          <DropdownMenuItem onSelect={() => setCredentialId(null)}>Connect later</DropdownMenuItem>
          {accounts.map(account => <DropdownMenuItem key={account.id} disabled={!projectReaderForCredential(account)}
            onSelect={() => setCredentialId(account.id)}>{account.displayName}
            {!projectReaderForCredential(account) && <span className='ml-2 text-xs'>Project reader unavailable</span>}
          </DropdownMenuItem>)}
        </DropdownMenuContent>
      </DropdownMenu>
      {connections.isError && <p role='status' className='text-sm text-[var(--text-muted)]'>Connected accounts could not be checked.</p>}
      {!connections.isFetching && !accounts.length && !connections.isError
        && <p className='text-sm text-[var(--text-muted)]'>No integration accounts are connected yet.</p>}
      <p className='text-sm text-[var(--text-muted)]'>Uses your existing Integrations accounts. Project reading currently supports Confluence pages.</p>
      <Link className='text-sm underline' href={`/workspace/${workspaceId}/integrations/confluence`}>Connect Confluence</Link>
    </div>}
    <div className='flex flex-col gap-2'>
      <label htmlFor={`${fieldId}-sources`} className='text-sm'>Confluence page links{!project && mode === 'create' ? ' (optional)' : ''}</label>
      <textarea id={`${fieldId}-sources`} className={fieldClass} rows={4} maxLength={16384}
        value={urls} onChange={event => setUrls(event.target.value)} disabled={disabled}
        required={!project && mode === 'import'} placeholder='Paste one page link per line' />
      <p className='text-sm text-[var(--text-muted)]'>Links are saved for setup. Document contents are read only after the account and source access are configured.</p>
    </div>
    {project && <div className='flex flex-col gap-2'>
      <label className='flex items-start gap-2 text-sm' htmlFor={`${fieldId}-allow-chat`}>
        <input type='checkbox' id={`${fieldId}-allow-chat`} className='mt-1' checked={!!model && chatModel === model}
          onChange={event => setChatModel(event.target.checked ? model ?? null : null)} disabled={disabled || !model || !credentialId} />
        <span>Allow my project chat to read these pages and send their contents to {model ?? 'the selected model'}.</span>
      </label>
      <p className='text-sm text-[var(--text-muted)]'>This project is private to its creator. Shared credentials use their own source permissions. Changing the model requires saving consent again.</p>
    </div>}
    {error && <p role='alert' className='text-sm text-[var(--text-error)]'>{error}</p>}
    {!canAdmin && <p role='status' className='text-sm'>A workspace administrator can create and configure projects.</p>}
    <div><Button type='submit' variant='primary' disabled={disabled || !name.trim()}>
      {update.isPending ? 'Saving...' : project ? 'Save project' : mode === 'import' ? 'Import project links' : 'Create project'}
    </Button></div>
  </form>
}

function Projects({workspaceId, selectedProjectId}: {workspaceId: string; selectedProjectId?: string}) {
  useOpenRouterChat(workspaceId)
  const {data: session} = useSession()
  const userId = session?.user?.id
  const {canAdmin} = useUserPermissionsContext()
  const {setup} = useProjectSetup(workspaceId, userId)
  const catalog = useProjectCatalog(workspaceId, userId)
  const [selectedId, setSelectedId] = useState(selectedProjectId)
  const [mode, setMode] = useState<'create' | 'import'>('create')
  const projects = setup.isError ? [] : setup.data?.projects ?? []
  const project = projects.find(value => value.project_id === selectedId)
  const status = !catalog.isError && !catalog.isFetching
    ? catalog.data?.projects.find(value => value.project_id === selectedId)?.setup_status : undefined

  return <div className='h-full min-w-0 overflow-y-auto bg-[var(--bg)] p-4 text-[var(--text-primary)] md:p-8'>
    <div className='mb-7 flex flex-wrap items-center justify-between gap-3'>
      <div><h1 className='text-2xl font-medium'>Projects</h1>
        <p className='mt-2 text-sm text-[var(--text-muted)]'>Create a project or bring in links to an existing one. You can connect sources later.</p></div>
      <Link className='text-sm underline' href={`/workspace/${workspaceId}/home`}>Back to chat</Link>
    </div>
    {setup.isPending ? <p role='status'>Loading projects...</p> : setup.isError ? <div role='alert'>
      <p>Projects could not be loaded.</p><Button onClick={() => {void setup.refetch()}}>Try again</Button>
    </div> : <div className='grid min-w-0 gap-8 lg:grid-cols-[240px_minmax(0,1fr)]'>
      <div className='flex min-w-0 flex-col items-stretch gap-2' aria-label='Your projects'>
        <Button onClick={() => {setSelectedId(undefined); setMode('create')}} disabled={!canAdmin}>Create project</Button>
        <Button onClick={() => {setSelectedId(undefined); setMode('import')}} disabled={!canAdmin}>Import project</Button>
        {projects.length === 0 && <p className='py-3 text-sm text-[var(--text-muted)]'>Your projects will appear here.</p>}
        {projects.map(value => <Button key={value.project_id} variant={selectedId === value.project_id ? 'active' : 'ghost'}
          className='min-w-0 justify-start' onClick={() => setSelectedId(value.project_id)}>
          <span className='truncate'>{value.name}</span>
        </Button>)}
      </div>
      <section className='min-w-0'>
        <h2 className='mb-4 break-words text-lg font-medium'>{project?.name ?? (mode === 'import' ? 'Import an existing project' : 'Create a project')}</h2>
        {project && <p role='status' className='mb-5 text-sm text-[var(--text-muted)]'>
          {catalog.isFetching ? 'Checking setup' : catalog.isError ? 'Access could not be checked'
            : labels[status ?? 'CONNECTION_REQUIRED']}. {status === 'ADMITTED'
            ? 'Return to chat to select this project.'
            : 'Source access must be authorized before project chat can use these documents.'}
        </p>}
        {selectedId && !project ? <p role='status'>This project setup is unavailable to your account.</p>
          : <ProjectForm key={project ? `${project.project_id}:${project.revision}` : mode}
            workspaceId={workspaceId} userId={userId} project={project} mode={mode}
            canAdmin={canAdmin} onSaved={setSelectedId} />}
      </section>
    </div>}
  </div>
}

export function ProjectSetupPage(props: {workspaceId: string; selectedProjectId?: string}) {
  const enabled = useFeatureFlag('openrouter-chat')
  return enabled ? <Projects {...props} /> : <p>Project setup is unavailable.</p>
}
