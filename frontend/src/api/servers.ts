import type {
  ConnectionTestResult,
  DeploymentBundleSecrets,
  Server,
  ServerCreateInput,
  ServerObservability,
} from '../types/server'

async function readApiError(res: Response, fallback: string): Promise<Error> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return new Error(body.detail)
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item ? String(item.msg) : '',
        )
        .filter(Boolean)
      if (messages.length > 0) return new Error(messages.join('；'))
    }
    return new Error(`${fallback}：${res.status}`)
  } catch {
    return new Error(`${fallback}：${res.status}`)
  }
}

export async function fetchServers(): Promise<Server[]> {
  const res = await fetch('/api/servers')
  if (!res.ok) {
    throw new Error(`获取服务器列表失败：${res.status}`)
  }
  return res.json()
}

export async function fetchServerObservability(id: string): Promise<ServerObservability> {
  const res = await fetch(`/api/servers/${id}/observability`)
  if (!res.ok) {
    throw await readApiError(res, '获取服务器指标失败')
  }
  return res.json()
}

export async function createServer(data: ServerCreateInput): Promise<Server> {
  const res = await fetch('/api/servers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    throw await readApiError(res, '新增服务器失败')
  }
  return res.json()
}

export async function updateServer(
  id: string,
  data: Partial<ServerCreateInput>,
): Promise<Server> {
  const res = await fetch(`/api/servers/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    throw await readApiError(res, '更新服务器失败')
  }
  return res.json()
}

export async function deleteServer(id: string): Promise<void> {
  const res = await fetch(`/api/servers/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    throw new Error(`删除服务器失败：${res.status}`)
  }
}

export async function testServerConnection(id: string): Promise<ConnectionTestResult> {
  const res = await fetch(`/api/servers/${id}/test-connection`, { method: 'POST' })
  if (!res.ok) {
    throw new Error(`测试连接失败：${res.status}`)
  }
  return res.json()
}

export interface DeploymentBundleDownload {
  blob: Blob
  filename: string
  sha256: string | null
}

export async function generateDeploymentBundle(
  id: string,
  secrets: DeploymentBundleSecrets,
): Promise<DeploymentBundleDownload> {
  const res = await fetch(`/api/servers/${id}/deployment-bundle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      secrets: {
        mysql_password: secrets.mysql_password || null,
        postgres_password: secrets.postgres_password || null,
        redis_password: secrets.redis_password || null,
      },
    }),
  })
  if (!res.ok) {
    throw await readApiError(res, '生成目标服务器安装包失败')
  }

  const disposition = res.headers.get('Content-Disposition') ?? ''
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i)
  return {
    blob: await res.blob(),
    filename: filenameMatch?.[1] ?? `aioms-target-server-${id}.tar.gz`,
    sha256: res.headers.get('X-Checksum-SHA256'),
  }
}
