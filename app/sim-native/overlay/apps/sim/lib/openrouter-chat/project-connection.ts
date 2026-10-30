import 'server-only'
import { getCredentialActorContext, canUseCredential } from '@/lib/credentials/access'
import { projectReaderForCredential } from '@/lib/project-integrations'

export async function projectConnection(id: string, workspaceId: string, userId: string) {
  const access = await getCredentialActorContext(id, userId)
  const credential = access.credential
  if (!credential || credential.workspaceId !== workspaceId || !canUseCredential(access)
      || (credential.type === 'oauth' && !credential.accountId)) return null
  const reader = projectReaderForCredential(credential)
  if (!reader) return null
  return {reader, credential: {id, type: credential.type, providerId: credential.providerId,
    accountId: credential.accountId, updatedAt: credential.updatedAt}}
}
