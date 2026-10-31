'use client'

import { useRef, useState } from 'react'
import { Button, Textarea, Tooltip } from '@sim/emcn'
import { Pencil, RefreshCw } from '@sim/emcn/icons'
import { MAX_CHAT_MESSAGE_LENGTH } from '@/lib/mothership/chat/context-limits'

interface SentMessageActionsProps {
  content: string
  disabled: boolean
  onResend: (content: string) => void | string | Promise<void | string>
  notice?: string
}

export function SentMessageActions({ content, disabled, onResend,
  notice = 'Sends a new message. Earlier messages stay in this chat.',
}: SentMessageActionsProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(content)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const sending = useRef(false)
  const editButton = useRef<HTMLButtonElement>(null)
  const blocked = disabled || pending
  const cancel = () => {
    setEditing(false); setError('')
    requestAnimationFrame(() => editButton.current?.focus())
  }
  const resend = async (text: string) => {
    if (blocked || sending.current || !text.trim()) return
    sending.current = true
    setPending(true); setError('')
    try {
      const problem = await onResend(text)
      if (problem) { setError(problem); return }
      setEditing(false)
    } catch {
      setError('Could not send this message. Try again.')
    } finally { sending.current = false; setPending(false) }
  }
  const buttonClass = 'flex size-[44px] items-center justify-center rounded-[6px] text-[var(--text-icon)] transition-colors hover-hover:bg-[var(--surface-hover)] focus-visible:outline focus-visible:outline-2 md:size-[26px] disabled:opacity-40'
  return <div className={editing || error ? 'w-full' : undefined}>
    {editing ? <form className='space-y-2' onSubmit={(event) => { event.preventDefault(); void resend(draft) }}>
      <Textarea autoFocus aria-label='Edit message' value={draft} rows={3} maxLength={MAX_CHAT_MESSAGE_LENGTH}
        disabled={blocked} onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') { event.preventDefault(); cancel() }
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) {
            event.preventDefault(); void resend(draft)
          }
        }} />
      <p className='text-[var(--text-muted)] text-xs'>{notice}</p>
      <div className='flex justify-end gap-2'>
        <Button type='button' variant='ghost' disabled={pending} onClick={cancel}>Cancel</Button>
        <Button type='submit' disabled={blocked || !draft.trim()}>Send again</Button>
      </div>
    </form> : <div className='flex justify-end gap-1'>
      <Tooltip.Root><Tooltip.Trigger asChild>
        <button ref={editButton} type='button' aria-label='Edit message' disabled={blocked} className={buttonClass}
          onClick={() => { setDraft(content); setError(''); setEditing(true) }}><Pencil className='size-[14px]' /></button>
      </Tooltip.Trigger><Tooltip.Content side='top'>Edit message</Tooltip.Content></Tooltip.Root>
      <Tooltip.Root><Tooltip.Trigger asChild>
        <button type='button' aria-label='Retry message' disabled={blocked} className={buttonClass}
          onClick={() => { void resend(content) }}><RefreshCw className='size-[14px]' /></button>
      </Tooltip.Trigger><Tooltip.Content side='top'>Retry message</Tooltip.Content></Tooltip.Root>
    </div>}
    {error && <p role='alert' className='mt-2 text-[var(--text-error)] text-xs'>{error}</p>}
  </div>
}
