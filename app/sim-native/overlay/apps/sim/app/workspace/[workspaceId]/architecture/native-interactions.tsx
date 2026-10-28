'use client'

import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction, type RefObject } from 'react'
import { Button, Popover, PopoverContent, PopoverItem, PopoverTrigger, Tooltip } from '@sim/emcn'
import { Cursor, Hand, Undo, Redo, Expand, ChevronDown } from '@sim/emcn/icons'
import { useReactFlow, type Node } from '@xyflow/react'
import { useCanvasModeStore } from '@/stores/canvas-mode'
import { applyPositions, capturePositions, recordLayout, undoLayout, redoLayout, type LayoutHistory, type Position } from './canvas-model'

export function useNativeInteractions<T extends Node>({ root, revision, enabled, setNodes, onClear }: {
  root: RefObject<HTMLElement | null>; revision?: string; enabled: boolean
  setNodes: Dispatch<SetStateAction<T[]>>; onClear: () => void
}) {
  const flow = useReactFlow<T>()
  const mode = useCanvasModeStore(state => state.mode)
  const setMode = useCanvasModeStore(state => state.setMode)
  const [history, setHistory] = useState<LayoutHistory>({ past: [], future: [] })
  const start = useRef<Position[] | null>(null)
  useEffect(() => { setHistory({ past: [], future: [] }); start.current = null }, [revision])
  const begin = useCallback(() => { start.current = capturePositions(flow.getNodes()) }, [flow])
  const finish = useCallback(() => {
    if (start.current) {
      const before = start.current
      const after = capturePositions(flow.getNodes())
      setHistory(previous => recordLayout(previous, before, after))
      start.current = null
    }
  }, [flow])
  const moveHistory = useCallback((direction: 'undo' | 'redo') => {
    if (!enabled) return
    const result = (direction === 'undo' ? undoLayout : redoLayout)(history, capturePositions(flow.getNodes()))
    if (result.positions) setNodes(nodes => applyPositions(nodes, result.positions!))
    setHistory(result.history)
  }, [enabled, history, flow, setNodes])
  const fit = useCallback(() => {
    const nodes = flow.getNodes().filter(node => !node.hidden)
    void flow.fitView({ nodes, padding: 0.16, maxZoom: 1, duration: 200 })
  }, [flow])
  const selectAll = useCallback(() => {
    const visible = new Set(flow.getNodes().filter(node => !node.hidden).map(node => node.id))
    setNodes(nodes => nodes.map(node => ({ ...node, selected: visible.has(node.id) })))
  }, [flow, setNodes])
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target
      if (!(target instanceof Element) || !root.current?.contains(target)
        || target.closest('input, textarea, select, [contenteditable="true"], [role="textbox"]')) return
      const command = event.ctrlKey || event.metaKey
      if (command && event.key.toLowerCase() === 'a') { event.preventDefault(); selectAll() }
      else if (command && event.key.toLowerCase() === 'z') {
        event.preventDefault(); moveHistory(event.shiftKey ? 'redo' : 'undo')
      } else if (command && event.key.toLowerCase() === 'y') { event.preventDefault(); moveHistory('redo') }
      else if (event.key === 'Escape') { onClear(); setNodes(nodes => nodes.map(node => ({ ...node, selected: false }))) }
      else if (event.key.toLowerCase() === 'v' && !command) setMode('cursor')
      else if (event.key.toLowerCase() === 'h' && !command) setMode('hand')
      else if (event.key.toLowerCase() === 'f' && !command) { event.preventDefault(); fit() }
    }
    document.addEventListener('keydown', keydown)
    return () => document.removeEventListener('keydown', keydown)
  }, [root, selectAll, moveHistory, onClear, setNodes, setMode, fit])
  return { mode, setMode, begin, finish, fit, selectAll, undo: () => moveHistory('undo'), redo: () => moveHistory('redo'),
    canUndo: enabled && history.past.length > 0, canRedo: enabled && history.future.length > 0 }
}

export function NativeCanvasControls({ actions }: { actions: {
  mode: 'hand' | 'cursor'; setMode: (mode: 'hand' | 'cursor') => void
  undo: () => void; redo: () => void; fit: () => void; canUndo: boolean; canRedo: boolean
} }) {
  return <div role='toolbar' aria-label='Canvas controls'
    className='absolute bottom-3 left-3 z-10 flex h-[36px] items-center gap-0.5 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] p-1'>
    <Popover>
      <PopoverTrigger asChild><Button variant='active' aria-label='Change canvas mode' className='h-[28px] gap-1 px-1.5'>
        {actions.mode === 'hand' ? <Hand className='size-[14px]' /> : <Cursor className='size-[14px]' />}
        <ChevronDown className='size-[12px]' />
      </Button></PopoverTrigger>
      <PopoverContent side='top' align='start'>
        <PopoverItem onClick={() => actions.setMode('hand')}><Hand className='size-[14px]' />Mover</PopoverItem>
        <PopoverItem onClick={() => actions.setMode('cursor')}><Cursor className='size-[14px]' />Pointer</PopoverItem>
      </PopoverContent>
    </Popover>
    <div className='mx-1 h-5 w-px bg-[var(--border)]' />
    {([{ label: 'Undo layout', Icon: Undo, onClick: actions.undo, disabled: !actions.canUndo },
      { label: 'Redo layout', Icon: Redo, onClick: actions.redo, disabled: !actions.canRedo },
      { label: 'Fit to View', Icon: Expand, onClick: actions.fit, disabled: false }]).map(({ label, Icon, onClick, disabled }) =>
      <Tooltip.Root key={label}><Tooltip.Trigger asChild>
        <Button variant='ghost' aria-label={label} className='size-[28px] p-0' disabled={disabled} onClick={onClick}>
          <Icon className='size-[14px]' />
        </Button>
      </Tooltip.Trigger><Tooltip.Content side='top'>{label}</Tooltip.Content></Tooltip.Root>)}
  </div>
}
