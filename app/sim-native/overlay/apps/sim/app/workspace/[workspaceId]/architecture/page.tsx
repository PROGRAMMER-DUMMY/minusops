import { Suspense } from 'react'
import { notFound } from 'next/navigation'
import { ArchitectureWorkspace } from './workspace'

export default function ArchitecturePage() {
  if (process.env.MINUS_OPENROUTER_CHAT !== 'true') notFound()
  return <Suspense fallback={null}><ArchitectureWorkspace /></Suspense>
}
