import { credentialProviderMatchesService, getServiceConfigByServiceId } from '@/lib/oauth/utils'

// Add a reader only after its reference validation, output bounds and access tests exist.
export const projectReaders = [{serviceId: 'confluence', id: 'confluence_retrieve@1.0.0'}] as const

export function projectReaderForCredential(credential: {providerId?: string | null; type?: string}) {
  if (!credential.providerId || !['oauth', 'service_account'].includes(credential.type ?? '')) return undefined
  return projectReaders.find(reader => {
    const service = getServiceConfigByServiceId(reader.serviceId)
    return service && credentialProviderMatchesService(credential.providerId!, service)
  })
}
