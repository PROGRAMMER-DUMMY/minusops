import { ProjectSetupPage } from './projects'

export default async function ProjectsPage({params, searchParams}: {
  params: Promise<{workspaceId: string}>
  searchParams: Promise<{project?: string | string[]}>
}) {
  const {workspaceId} = await params
  const query = await searchParams
  return <ProjectSetupPage workspaceId={workspaceId}
    selectedProjectId={typeof query.project === 'string' ? query.project : undefined} />
}
