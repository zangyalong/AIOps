<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  createService,
  createServiceFileAccess,
  createServiceInstance,
  deleteService,
  deleteServiceFileAccess,
  deleteServiceInstance,
  fetchServiceInstanceStatuses,
  fetchServiceFileAccesses,
  fetchServiceInstances,
  fetchServiceObservability,
  fetchServices,
  updateService,
  updateServiceControlAuthorization,
  updateServiceInstance,
} from '../api/services'
import { fetchServerObservability, fetchServers } from '../api/servers'
import MetricTrendChart from '../components/MetricTrendChart.vue'
import type { Server, ServerObservability } from '../types/server'
import type {
  ServiceInput,
  ServiceInstance,
  ServiceInstanceInput,
  ServiceRecord,
  RuntimeType,
  RuntimeStatus,
  ServiceInstanceStatus,
  ServiceMetric,
  ServiceObservability,
  ServiceFileAccess,
} from '../types/service'

const route = useRoute()
const router = useRouter()
const services = ref<ServiceRecord[]>([])
const instances = ref<ServiceInstance[]>([])
const servers = ref<Server[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const formError = ref<string | null>(null)
const saving = ref(false)
const search = ref('')
const selectedServerId = ref<string | null>(null)
const expandedServerIds = ref<Set<string>>(new Set())
const instanceStatuses = ref<Record<string, ServiceInstanceStatus>>({})
const statusLoadingInstanceIds = ref<Set<string>>(new Set())
const observabilityByInstance = ref<Record<string, ServiceObservability>>({})
const observabilityLoadingIds = ref<Set<string>>(new Set())
const observabilityErrors = ref<Record<string, string>>({})
const observabilityMinutes = ref(1440)
const controlAuthorizationSavingIds = ref<Set<string>>(new Set())
const fileAccesses = ref<ServiceFileAccess[]>([])
const fileAccessSavingIds = ref<Set<string>>(new Set())
const fileAccessDrafts = reactive<Record<string, { path: string; access_level: 'read' | 'write' }>>({})
const serverObservabilityById = ref<Record<string, ServerObservability>>({})
const serverObservabilityLoadingIds = ref<Set<string>>(new Set())
const serverObservabilityErrors = ref<Record<string, string>>({})
const serverObservabilityMinutes = ref(60)

const serviceModal = ref(false)
const editingService = ref<ServiceRecord | null>(null)
const serviceForm = reactive({
  name: '',
  environment: 'prod',
  description: '',
})

const instanceModal = ref(false)
const editingInstance = ref<ServiceInstance | null>(null)
const instanceForm = reactive({
  name: '',
  server_id: '',
  runtime_type: '' as RuntimeType | '',
  runtime_name: '',
  metric_instance: '',
  log_service: '',
  description: '',
})

const selectedId = computed(() => {
  const value = route.params.serviceId
  return typeof value === 'string' ? value : null
})
const selectedService = computed(
  () => services.value.find((service) => service.id === selectedId.value) ?? null,
)
const serverMap = computed(() => new Map(servers.value.map((server) => [server.id, server])))
const selectedServer = computed(
  () => servers.value.find((server) => server.id === selectedServerId.value) ?? null,
)
const selectedServerObservability = computed(() =>
  selectedServerId.value
    ? serverObservabilityById.value[selectedServerId.value] ?? null
    : null,
)
const selectedServerInstances = computed(() =>
  instances.value.filter((instance) => instance.server_id === selectedServerId.value),
)
const selectedServerServices = computed(() =>
  selectedServerId.value ? servicesForServer(selectedServerId.value) : [],
)
const allSelectedInstances = computed(() =>
  instances.value.filter((instance) => instance.service_id === selectedId.value),
)
const selectedInstances = computed(() => {
  if (!selectedServerId.value) return allSelectedInstances.value
  return allSelectedInstances.value.filter(
    (instance) => instance.server_id === selectedServerId.value,
  )
})
const selectedInstanceKey = computed(() =>
  selectedInstances.value.map((instance) => instance.id).sort().join(','),
)
const filteredServers = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return servers.value
  return servers.value.filter(
    (server) =>
      [server.name, server.ip_address].some((value) =>
        value.toLowerCase().includes(keyword),
      ) ||
      servicesForServer(server.id).some((service) => serviceMatchesSearch(service, keyword)),
  )
})

function servicesForServer(serverId: string): ServiceRecord[] {
  const serviceIds = new Set(
    instances.value
      .filter((instance) => instance.server_id === serverId)
      .map((instance) => instance.service_id),
  )
  return services.value.filter((service) => serviceIds.has(service.id))
}

function serviceMatchesSearch(service: ServiceRecord, keyword: string): boolean {
  return [service.name, service.environment].some((value) =>
    value.toLowerCase().includes(keyword),
  )
}

function visibleServicesForServer(server: Server): ServiceRecord[] {
  const serverServices = servicesForServer(server.id)
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return serverServices
  if (
    [server.name, server.ip_address].some((value) =>
      value.toLowerCase().includes(keyword),
    )
  ) {
    return serverServices
  }
  return serverServices.filter((service) => serviceMatchesSearch(service, keyword))
}

function serverServiceCount(serverId: string): number {
  return servicesForServer(serverId).length
}

function serviceInstanceCountOnServer(serviceId: string, serverId: string): number {
  return instances.value.filter(
    (instance) =>
      instance.service_id === serviceId && instance.server_id === serverId,
  ).length
}

function instancesForServiceOnServer(serviceId: string, serverId: string): ServiceInstance[] {
  return instances.value.filter(
    (instance) => instance.service_id === serviceId && instance.server_id === serverId,
  )
}

function aggregateRuntimeStatus(rows: ServiceInstance[]): RuntimeStatus {
  const statuses = rows
    .map((instance) => instanceStatuses.value[instance.id]?.status)
    .filter((status): status is RuntimeStatus => Boolean(status))
  if (statuses.includes('error')) return 'error'
  if (statuses.includes('running')) return 'running'
  if (statuses.length > 0 && statuses.every((status) => status === 'stopped')) return 'stopped'
  return 'unknown'
}

function serviceRuntimeStatus(serviceId: string, serverId: string): RuntimeStatus {
  return aggregateRuntimeStatus(instancesForServiceOnServer(serviceId, serverId))
}

function serviceStatusLabel(serviceId: string, serverId: string): string {
  const rows = instancesForServiceOnServer(serviceId, serverId)
  if (rows.some((instance) => statusLoadingInstanceIds.value.has(instance.id))) {
    return '检测中'
  }
  const labels: Record<RuntimeStatus, string> = {
    running: '运行',
    stopped: '停止',
    error: '异常',
    unknown: '未检测',
  }
  return labels[serviceRuntimeStatus(serviceId, serverId)]
}

function serviceStatusDetail(serviceId: string, serverId: string): string {
  const rows = instancesForServiceOnServer(serviceId, serverId)
  const details = rows
    .map((instance) => instanceStatuses.value[instance.id]?.detail)
    .filter(Boolean)
  return details.join('；') || '展开服务器或点击检测状态后获取实时结果'
}

function instanceStatus(instance: ServiceInstance): ServiceInstanceStatus | null {
  return instanceStatuses.value[instance.id] ?? null
}

function instanceStatusLabel(instance: ServiceInstance): string {
  if (statusLoadingInstanceIds.value.has(instance.id)) return '检测中'
  return instanceStatus(instance)?.label ?? '未检测'
}

function instanceStatusClass(instance: ServiceInstance): RuntimeStatus {
  return instanceStatus(instance)?.status ?? 'unknown'
}

function instancePortsLabel(instance: ServiceInstance): string {
  const status = instanceStatus(instance)
  if (!status) return '检测状态后获取'
  if (status.ports?.length) return status.ports.join('、')
  return status.status === 'running' ? '未发现 TCP/UDP 监听端口' : '无监听端口'
}

function metricSourceLabel(instance: ServiceInstance): string {
  if (instance.metric_instance) return `Prometheus ${instance.metric_instance}`
  const identity = `${selectedService.value?.name ?? ''} ${instance.runtime_name ?? ''}`.toLowerCase()
  if (identity.includes('node-exporter')) return 'Node Exporter（自动匹配）'
  if (identity.includes('mysql')) return 'MySQL Exporter（自动匹配）'
  if (identity.includes('postgres')) return 'PostgreSQL Exporter（自动匹配）'
  if (identity.includes('redis')) return 'Redis Exporter（自动匹配）'
  if (instance.runtime_type === 'systemd') return 'cAdvisor（systemd cgroup）'
  if (instance.runtime_type === 'docker') return 'cAdvisor（容器标签）'
  return '未接入服务级指标'
}

function statusTargetInstanceIds(filters: { serverId?: string; serviceId?: string }): string[] {
  return instances.value
    .filter(
      (instance) =>
        (!filters.serverId || instance.server_id === filters.serverId) &&
        (!filters.serviceId || instance.service_id === filters.serviceId),
    )
    .map((instance) => instance.id)
}

function setStatusLoading(instanceIds: string[], loadingState: boolean) {
  const next = new Set(statusLoadingInstanceIds.value)
  for (const instanceId of instanceIds) {
    if (loadingState) next.add(instanceId)
    else next.delete(instanceId)
  }
  statusLoadingInstanceIds.value = next
}

async function refreshStatuses(filters: { serverId?: string; serviceId?: string }) {
  const targetInstanceIds = statusTargetInstanceIds(filters)
  setStatusLoading(targetInstanceIds, true)
  try {
    const rows = await fetchServiceInstanceStatuses(filters)
    for (const row of rows) instanceStatuses.value[row.instance_id] = row
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    setStatusLoading(targetInstanceIds, false)
  }
}

async function refreshServerStatuses(serverId: string) {
  await refreshStatuses({ serverId })
}

function setServerObservabilityLoading(serverId: string, loadingState: boolean) {
  const next = new Set(serverObservabilityLoadingIds.value)
  if (loadingState) next.add(serverId)
  else next.delete(serverId)
  serverObservabilityLoadingIds.value = next
}

async function refreshServerObservability(serverId: string) {
  setServerObservabilityLoading(serverId, true)
  delete serverObservabilityErrors.value[serverId]
  try {
    serverObservabilityById.value[serverId] = await fetchServerObservability(
      serverId,
      serverObservabilityMinutes.value,
    )
  } catch (e) {
    serverObservabilityErrors.value[serverId] = e instanceof Error ? e.message : String(e)
  } finally {
    setServerObservabilityLoading(serverId, false)
  }
}

async function refreshSelectedServerOverview() {
  if (!selectedServerId.value) return
  await Promise.all([
    refreshServerStatuses(selectedServerId.value),
    refreshServerObservability(selectedServerId.value),
  ])
}

async function refreshSelectedStatuses() {
  if (!selectedId.value) return
  await refreshStatuses({
    serviceId: selectedId.value,
    serverId: selectedServerId.value ?? undefined,
  })
}

function setObservabilityLoading(instanceId: string, loadingState: boolean) {
  const next = new Set(observabilityLoadingIds.value)
  if (loadingState) next.add(instanceId)
  else next.delete(instanceId)
  observabilityLoadingIds.value = next
}

async function refreshSelectedObservability() {
  const rows = selectedInstances.value
  await Promise.all(
    rows.map(async (instance) => {
      setObservabilityLoading(instance.id, true)
      delete observabilityErrors.value[instance.id]
      try {
        observabilityByInstance.value[instance.id] = await fetchServiceObservability(
          instance.id,
          observabilityMinutes.value,
        )
      } catch (e) {
        observabilityErrors.value[instance.id] = e instanceof Error ? e.message : String(e)
      } finally {
        setObservabilityLoading(instance.id, false)
      }
    }),
  )
}

function formatMetric(metric: ServiceMetric): string {
  if (metric.unit === 'state') return metric.value >= 1 ? '正常' : '异常'
  if (metric.unit === 'percent') return `${metric.value.toFixed(1)}%`
  if (metric.unit === 'bytes') {
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    let value = metric.value
    let index = 0
    while (Math.abs(value) >= 1024 && index < units.length - 1) {
      value /= 1024
      index += 1
    }
    return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
  }
  if (metric.unit === 'seconds') return `${(metric.value * 1000).toFixed(0)} ms`
  if (metric.unit === 'duration') {
    const totalMinutes = Math.max(0, Math.floor(metric.value / 60))
    const days = Math.floor(totalMinutes / 1440)
    const hours = Math.floor((totalMinutes % 1440) / 60)
    if (days > 0) return `${days} 天 ${hours} 小时`
    if (hours > 0) return `${hours} 小时 ${totalMinutes % 60} 分钟`
    return `${totalMinutes} 分钟`
  }
  return Number.isInteger(metric.value) ? String(metric.value) : metric.value.toFixed(2)
}

function metricIsBad(metric: ServiceMetric): boolean {
  return metric.unit === 'state' && metric.value < 1
}

function formatLogTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

function runtimeActionAvailable(instance: ServiceInstance): boolean {
  return Boolean(
    instance.server_id &&
      instance.runtime_name &&
      (instance.runtime_type === 'systemd' || instance.runtime_type === 'docker') &&
      instance.control_enabled,
  )
}

function controlAuthorizationAvailable(instance: ServiceInstance): boolean {
  if (!instance.server_id || !instance.runtime_name) return false
  if (instance.runtime_type === 'systemd') return instance.runtime_name.endsWith('.service')
  return instance.runtime_type === 'docker'
}

function fileAccessesForInstance(instanceId: string): ServiceFileAccess[] {
  return fileAccesses.value.filter((item) => item.service_instance_id === instanceId)
}

function fileAccessDraft(instanceId: string) {
  if (!fileAccessDrafts[instanceId]) {
    fileAccessDrafts[instanceId] = { path: '', access_level: 'read' }
  }
  return fileAccessDrafts[instanceId]
}

async function addFileAccess(instance: ServiceInstance) {
  if (!instance.server_id || fileAccessSavingIds.value.has(instance.id)) return
  const draft = fileAccessDraft(instance.id)
  const path = draft.path.trim()
  if (!path.startsWith('/')) {
    error.value = '文件授权必须填写目标服务器上的绝对路径'
    return
  }
  const label = draft.access_level === 'read' ? '可读目录或文件' : '精确可写文件'
  if (!confirm(`确定为 ${instance.name} 授权${label}？\n\n${path}\n\n写入仍需在对话中预览差异并进行高风险二次确认。`)) return
  fileAccessSavingIds.value = new Set([...fileAccessSavingIds.value, instance.id])
  error.value = null
  try {
    const saved = await createServiceFileAccess(instance.id, {
      access_level: draft.access_level,
      path,
    })
    if (!fileAccesses.value.some((item) => item.id === saved.id)) fileAccesses.value.push(saved)
    draft.path = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    const next = new Set(fileAccessSavingIds.value)
    next.delete(instance.id)
    fileAccessSavingIds.value = next
  }
}

async function removeFileAccess(access: ServiceFileAccess) {
  if (!confirm(`确定移除文件授权？\n\n${access.path}`)) return
  error.value = null
  try {
    await deleteServiceFileAccess(access.id)
    fileAccesses.value = fileAccesses.value.filter((item) => item.id !== access.id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function toggleControlAuthorization(instance: ServiceInstance) {
  if (!controlAuthorizationAvailable(instance)) return
  const enabled = !instance.control_enabled
  const confirmation = enabled
    ? `确定为 ${instance.runtime_name} 开启控制授权？\n\n系统会同步加入目标服务器 root 白名单；开启后，AI 可以在人工审批通过后发起启动、停止或重启。`
    : `确定关闭 ${instance.runtime_name} 的控制授权？\n\n系统会同时从目标服务器 root 白名单移除该服务，之后 AI 将不能再发起启动、停止或重启。`
  if (!confirm(confirmation)) {
    return
  }

  controlAuthorizationSavingIds.value = new Set([
    ...controlAuthorizationSavingIds.value,
    instance.id,
  ])
  error.value = null
  try {
    const saved = await updateServiceControlAuthorization(instance.id, enabled)
    const index = instances.value.findIndex((item) => item.id === saved.id)
    if (index !== -1) instances.value[index] = saved
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    const next = new Set(controlAuthorizationSavingIds.value)
    next.delete(instance.id)
    controlAuthorizationSavingIds.value = next
  }
}

function requestRuntimeAction(instance: ServiceInstance, action: 'start' | 'stop') {
  if (!runtimeActionAvailable(instance)) return
  const server = instance.server_id ? serverMap.value.get(instance.server_id) : null
  const verb = action === 'start' ? '启动' : '停止'
  const prompt = [
    `请在 ${server?.name ?? '目标服务器'} 上${verb}服务 ${instance.runtime_name}。`,
    `运行类型是 ${instance.runtime_type}。`,
    '请先检测当前状态并核对目标；如果已有受控写工具就发起人工审批，未配置写网关时不要直接执行。',
  ].join('')
  void router.push({ name: 'chat', query: { prompt } })
}

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const [serviceRows, instanceRows, serverRows, fileAccessRows] = await Promise.all([
      fetchServices(),
      fetchServiceInstances(),
      fetchServers(),
      fetchServiceFileAccesses(),
    ])
    services.value = serviceRows
    instances.value = instanceRows
    servers.value = serverRows
    fileAccesses.value = fileAccessRows
    if (selectedId.value && !serviceRows.some((service) => service.id === selectedId.value)) {
      await router.replace('/services')
    } else if (selectedId.value) {
      syncSelectedServerToService(selectedId.value)
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
watch(
  selectedId,
  (serviceId) => {
    if (serviceId) syncSelectedServerToService(serviceId)
  },
  { immediate: true },
)
watch(
  selectedInstanceKey,
  (key) => {
    if (!key) return
    void refreshSelectedObservability()
  },
)
watch(observabilityMinutes, () => {
  if (selectedInstanceKey.value) void refreshSelectedObservability()
})
watch(serverObservabilityMinutes, () => {
  if (selectedServerId.value && !selectedId.value) {
    void refreshServerObservability(selectedServerId.value)
  }
})

function syncSelectedServerToService(serviceId: string) {
  const selectedServerHasService = instances.value.some(
    (instance) =>
      instance.service_id === serviceId && instance.server_id === selectedServerId.value,
  )
  if (!selectedServerHasService) {
    selectedServerId.value =
      instances.value.find(
        (instance) => instance.service_id === serviceId && instance.server_id !== null,
      )?.server_id ?? null
  }
  if (selectedServerId.value) {
    markServerExpanded(selectedServerId.value)
    void refreshStatuses({ serviceId, serverId: selectedServerId.value })
  }
}

function openService(serviceId: string) {
  if (selectedId.value === serviceId) syncSelectedServerToService(serviceId)
  void router.push(`/services/${serviceId}`)
}

function openServiceForServer(serviceId: string, serverId: string) {
  selectedServerId.value = serverId
  markServerExpanded(serverId)
  if (selectedId.value === serviceId) {
    void refreshStatuses({ serviceId, serverId })
  }
  void router.push(`/services/${serviceId}`)
}

function markServerExpanded(serverId: string) {
  const next = new Set(expandedServerIds.value)
  next.add(serverId)
  expandedServerIds.value = next
}

function isServerExpanded(serverId: string): boolean {
  return Boolean(search.value.trim()) || expandedServerIds.value.has(serverId)
}

function toggleServer(serverId: string) {
  selectedServerId.value = serverId
  void router.push('/services')
  void refreshSelectedServerOverview()
  const next = new Set(expandedServerIds.value)
  if (next.has(serverId)) {
    next.delete(serverId)
  } else {
    next.add(serverId)
  }
  expandedServerIds.value = next
}

function serverRuntimeCount(status: RuntimeStatus): number {
  return selectedServerInstances.value.filter(
    (instance) => instanceStatuses.value[instance.id]?.status === status,
  ).length
}

function serverInstancePorts(serviceId: string): string {
  const ports = instancesForServiceOnServer(serviceId, selectedServerId.value ?? '')
    .flatMap((instance) => instanceStatuses.value[instance.id]?.ports ?? [])
  return [...new Set(ports)].join('、') || '未发现监听端口'
}

function resetServiceForm() {
  editingService.value = null
  serviceForm.name = ''
  serviceForm.environment = 'prod'
  serviceForm.description = ''
  formError.value = null
}

function openCreateService() {
  resetServiceForm()
  serviceModal.value = true
}

function openEditService(service: ServiceRecord) {
  editingService.value = service
  serviceForm.name = service.name
  serviceForm.environment = service.environment
  serviceForm.description = service.description ?? ''
  formError.value = null
  serviceModal.value = true
}

async function submitService() {
  if (!serviceForm.name.trim() || !serviceForm.environment.trim()) {
    formError.value = '服务名和环境是必填项'
    return
  }
  const payload: ServiceInput = {
    name: serviceForm.name.trim(),
    environment: serviceForm.environment.trim(),
    description: serviceForm.description.trim() || null,
  }
  saving.value = true
  formError.value = null
  try {
    const saved = editingService.value
      ? await updateService(editingService.value.id, payload)
      : await createService(payload)
    const index = services.value.findIndex((service) => service.id === saved.id)
    if (index === -1) services.value.push(saved)
    else services.value[index] = saved
    services.value.sort((left, right) =>
      `${left.environment}/${left.name}`.localeCompare(`${right.environment}/${right.name}`),
    )
    serviceModal.value = false
    if (!editingService.value) openService(saved.id)
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function removeService(service: ServiceRecord) {
  if (!confirm(`确定删除服务 ${service.name}/${service.environment}？它的实例也会删除。`)) {
    return
  }
  error.value = null
  try {
    await deleteService(service.id)
    services.value = services.value.filter((item) => item.id !== service.id)
    instances.value = instances.value.filter((item) => item.service_id !== service.id)
    if (selectedId.value === service.id) await router.push('/services')
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

function resetInstanceForm() {
  editingInstance.value = null
  instanceForm.name = ''
  instanceForm.server_id = ''
  instanceForm.runtime_type = ''
  instanceForm.runtime_name = ''
  instanceForm.metric_instance = ''
  instanceForm.log_service = ''
  instanceForm.description = ''
  formError.value = null
}

function openCreateInstance() {
  resetInstanceForm()
  instanceForm.server_id = selectedServerId.value ?? ''
  instanceModal.value = true
}

function openEditInstance(instance: ServiceInstance) {
  editingInstance.value = instance
  instanceForm.name = instance.name
  instanceForm.server_id = instance.server_id ?? ''
  instanceForm.runtime_type = instance.runtime_type ?? ''
  instanceForm.runtime_name = instance.runtime_name ?? ''
  instanceForm.metric_instance = instance.metric_instance ?? ''
  instanceForm.log_service = instance.log_service ?? ''
  instanceForm.description = instance.description ?? ''
  formError.value = null
  instanceModal.value = true
}

async function submitInstance() {
  if (!selectedService.value || !instanceForm.name.trim()) {
    formError.value = '实例名称是必填项'
    return
  }
  const runtimeName = instanceForm.runtime_name.trim()
  if (Boolean(instanceForm.runtime_type) !== Boolean(runtimeName)) {
    formError.value = '运行类型和运行对象必须同时填写'
    return
  }
  if (instanceForm.runtime_type && !instanceForm.server_id) {
    formError.value = '填写运行时身份时必须绑定服务器'
    return
  }
  const payload: ServiceInstanceInput = {
    service_id: selectedService.value.id,
    server_id: instanceForm.server_id || null,
    name: instanceForm.name.trim(),
    runtime_type: instanceForm.runtime_type || null,
    runtime_name: runtimeName || null,
    metric_instance: instanceForm.metric_instance.trim() || null,
    log_service: instanceForm.log_service.trim() || null,
    description: instanceForm.description.trim() || null,
  }
  saving.value = true
  formError.value = null
  try {
    const saved = editingInstance.value
      ? await updateServiceInstance(editingInstance.value.id, payload)
      : await createServiceInstance(payload)
    const index = instances.value.findIndex((instance) => instance.id === saved.id)
    if (index === -1) instances.value.push(saved)
    else instances.value[index] = saved
    if (saved.server_id) {
      selectedServerId.value = saved.server_id
      markServerExpanded(saved.server_id)
      void refreshStatuses({ serviceId: saved.service_id, serverId: saved.server_id })
    }
    instanceModal.value = false
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function removeInstance(instance: ServiceInstance) {
  if (!confirm(`确定删除实例 ${instance.name}？`)) return
  error.value = null
  try {
    await deleteServiceInstance(instance.id)
    instances.value = instances.value.filter((item) => item.id !== instance.id)
    if (
      selectedServerId.value &&
      !instances.value.some(
        (item) =>
          item.service_id === instance.service_id &&
          item.server_id === selectedServerId.value,
      )
    ) {
      await router.push('/services')
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

function serverLabel(serverId: string | null): string {
  if (!serverId) return '未绑定服务器'
  const server = serverMap.value.get(serverId)
  return server ? `${server.name}（${server.ip_address}）` : '服务器记录不存在'
}

function serverStatusLabel(server: Server): string {
  const labels: Record<Server['status'], string> = {
    unknown: '未检测',
    active: '可连接',
    unreachable: '不可达',
    unconfigured: '未配置 SSH',
  }
  return labels[server.status]
}

</script>

<template>
  <div class="services-view">
    <header class="view-header">
      <div>
        <span class="view-kicker">CATALOG / RUNTIME SERVICES</span>
        <h1>服务目录</h1>
        <p class="subtitle">按服务器查看服务状态、关键指标和近期日志</p>
      </div>
      <button type="button" class="btn-primary" @click="openCreateService">新增服务</button>
    </header>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading" class="hint">加载服务目录…</p>

    <div v-else class="directory-layout">
      <aside class="directory-menu-panel">
        <div class="panel-heading">
          <h2>服务器与服务</h2>
          <span class="panel-count">{{ servers.length }}</span>
        </div>
        <input v-model="search" class="directory-search" placeholder="搜索服务器或服务" />
        <p v-if="servers.length === 0" class="list-empty">还没有纳管服务器</p>
        <p v-else-if="filteredServers.length === 0" class="list-empty">没有符合条件的服务器或服务</p>

        <nav v-else class="server-tree" aria-label="服务器与服务目录">
          <section v-for="server in filteredServers" :key="server.id" class="server-menu-group">
            <button
              type="button"
              class="server-menu-item"
              :class="{ active: server.id === selectedServerId }"
              :aria-expanded="isServerExpanded(server.id)"
              @click="toggleServer(server.id)"
            >
              <span class="menu-chevron" :class="{ expanded: isServerExpanded(server.id) }">›</span>
              <span class="server-menu-main">
                <span class="server-menu-name">{{ server.name }}</span>
                <span class="server-menu-address">{{ server.ip_address }}</span>
              </span>
              <span class="server-menu-side">
                <span class="server-state" :class="`status-${server.status}`">
                  {{ serverStatusLabel(server) }}
                </span>
                <span>{{ serverServiceCount(server.id) }} 个服务</span>
              </span>
            </button>

            <div v-if="isServerExpanded(server.id)" class="service-submenu">
              <p v-if="visibleServicesForServer(server).length === 0" class="submenu-empty">
                该服务器还没有登记服务
              </p>
              <button
                v-for="service in visibleServicesForServer(server)"
                :key="service.id"
                type="button"
                class="service-submenu-item"
                :class="{
                  active: service.id === selectedId && server.id === selectedServerId,
                }"
                @click="openServiceForServer(service.id, server.id)"
              >
                <span class="submenu-service-heading">
                  <span class="service-name">{{ service.name }}</span>
                  <span
                    class="runtime-status"
                    :class="`runtime-${serviceRuntimeStatus(service.id, server.id)}`"
                    :title="serviceStatusDetail(service.id, server.id)"
                  >
                    <span class="status-light" aria-hidden="true" />
                    {{ serviceStatusLabel(service.id, server.id) }}
                  </span>
                </span>
                <span class="service-counts">
                  {{ serviceInstanceCountOnServer(service.id, server.id) }} 个实例
                </span>
              </button>
            </div>
          </section>
        </nav>
      </aside>

      <main v-if="selectedService" class="service-detail">
        <div class="detail-header">
          <div>
            <div class="detail-title">
              <h2>{{ selectedService.name }}</h2>
              <span class="environment-badge">{{ selectedService.environment }}</span>
            </div>
            <p>{{ selectedService.description || '暂无服务说明' }}</p>
          </div>
          <div class="header-actions">
            <button type="button" class="btn-ghost" @click="openEditService(selectedService)">
              编辑
            </button>
            <button type="button" class="btn-ghost danger" @click="removeService(selectedService)">
              删除
            </button>
          </div>
        </div>

        <section class="detail-section observability-section">
          <div class="section-heading observability-heading">
            <div>
              <h3>实时状态与可观测性</h3>
              <p>直接查看 Prometheus 关键指标和 Loki 近期日志；状态来自目标服务器只读检测</p>
            </div>
            <div class="observability-controls">
              <select v-model.number="observabilityMinutes" aria-label="可观测性时间范围">
                <option :value="60">最近 1 小时</option>
                <option :value="1440">最近 24 小时</option>
                <option :value="10080">最近 7 天</option>
              </select>
              <button type="button" class="btn-secondary" @click="refreshSelectedStatuses">
                检测状态
              </button>
              <button type="button" class="btn-secondary" @click="refreshSelectedObservability">
                刷新数据
              </button>
            </div>
          </div>

          <p v-if="selectedInstances.length === 0" class="section-empty">
            该服务器上还没有可展示的服务实例。
          </p>
          <div v-else class="observability-stack">
            <article v-for="instance in selectedInstances" :key="instance.id" class="observability-card">
              <div class="observability-instance-head">
                <div>
                  <strong>{{ instance.runtime_name || instance.name }}</strong>
                  <span>{{ instance.runtime_type || '未配置运行时' }}</span>
                </div>
                <span
                  class="runtime-status prominent"
                  :class="`runtime-${instanceStatusClass(instance)}`"
                  :title="instanceStatus(instance)?.detail || '尚未检测实时状态'"
                >
                  <span class="status-light" aria-hidden="true" />
                  {{ instanceStatusLabel(instance) }}
                </span>
              </div>

              <p v-if="observabilityLoadingIds.has(instance.id)" class="hint compact">
                正在读取 Prometheus 与 Loki…
              </p>
              <p v-else-if="observabilityErrors[instance.id]" class="error compact">
                {{ observabilityErrors[instance.id] }}
              </p>
              <template v-else-if="observabilityByInstance[instance.id]">
                <div class="observability-grid">
                  <section class="metric-panel">
                    <div class="panel-subheading">
                      <h4>关键指标</h4>
                      <span>{{ metricSourceLabel(instance) }}</span>
                    </div>
                    <div v-if="observabilityByInstance[instance.id].metrics.length" class="metric-grid">
                      <div
                        v-for="metric in observabilityByInstance[instance.id].metrics"
                        :key="metric.key"
                        class="metric-card"
                        :class="{ bad: metricIsBad(metric) }"
                      >
                        <span>{{ metric.label }}</span>
                        <strong>{{ formatMetric(metric) }}</strong>
                      </div>
                    </div>
                    <p v-else class="data-empty">
                      {{ observabilityByInstance[instance.id].metric_message }}
                    </p>
                  </section>

                  <section class="log-panel">
                    <div class="panel-subheading">
                      <h4>近期日志</h4>
                      <span>{{ instance.log_service || '未配置 Loki 标签' }}</span>
                    </div>
                    <div v-if="observabilityByInstance[instance.id].logs.length" class="log-list">
                      <div
                        v-for="log in observabilityByInstance[instance.id].logs"
                        :key="`${log.timestamp}-${log.line}`"
                        class="log-row"
                      >
                        <time>{{ formatLogTime(log.timestamp) }}</time>
                        <span class="log-level" :class="`log-${log.level}`">{{ log.level }}</span>
                        <code>{{ log.line }}</code>
                      </div>
                    </div>
                    <p v-else class="data-empty">
                      {{ observabilityByInstance[instance.id].log_message }}
                    </p>
                  </section>
                </div>
              </template>
            </article>
          </div>
        </section>

        <section class="detail-section">
          <div class="section-heading">
            <div>
              <h3>{{ selectedServer ? `${selectedServer.name} 的部署实例` : '部署实例' }}</h3>
              <p>
                {{
                  selectedServer
                    ? `这里只展示当前服务器上的实例；该逻辑服务共 ${allSelectedInstances.length} 个实例`
                    : '将逻辑服务映射到服务器、Prometheus 和 Loki 标签'
                }}
              </p>
            </div>
            <button type="button" class="btn-secondary" @click="openCreateInstance">+ 添加实例</button>
          </div>
          <p v-if="selectedInstances.length === 0" class="section-empty">还没有部署实例。</p>
          <div v-else class="instance-grid">
            <article v-for="instance in selectedInstances" :key="instance.id" class="instance-card">
              <div class="card-heading">
                <div class="instance-title">
                  <h4>{{ instance.name }}</h4>
                  <span
                    class="runtime-status"
                    :class="`runtime-${instanceStatusClass(instance)}`"
                    :title="instanceStatus(instance)?.detail || '尚未检测实时状态'"
                  >
                    <span class="status-light" aria-hidden="true" />
                    {{ instanceStatusLabel(instance) }}
                  </span>
                </div>
                <div class="inline-actions">
                  <button type="button" @click="openEditInstance(instance)">编辑</button>
                  <button type="button" class="danger" @click="removeInstance(instance)">删除</button>
                </div>
              </div>
              <p>{{ serverLabel(instance.server_id) }}</p>
              <dl>
                <div>
                  <dt>运行时</dt>
                  <dd>
                    {{
                      instance.runtime_type && instance.runtime_name
                        ? `${instance.runtime_type}/${instance.runtime_name}`
                        : '未配置'
                    }}
                  </dd>
                </div>
                <div><dt>监听端口</dt><dd>{{ instancePortsLabel(instance) }}</dd></div>
                <div><dt>指标来源</dt><dd>{{ metricSourceLabel(instance) }}</dd></div>
                <div><dt>Loki</dt><dd>{{ instance.log_service || '未配置' }}</dd></div>
              </dl>
              <p v-if="instance.description" class="instance-description">{{ instance.description }}</p>
              <div
                v-if="instance.runtime_type === 'systemd' || instance.runtime_type === 'docker'"
                class="control-authorization"
              >
                <div>
                  <strong>服务控制授权</strong>
                  <span>
                    {{
                      instance.control_enabled
                        ? 'AIOMS 与目标服务器白名单已同步授权'
                        : '开启后将同步服务器白名单；操作仍需人工审批'
                    }}
                  </span>
                </div>
                <button
                  type="button"
                  class="control-switch"
                  :class="{ active: instance.control_enabled }"
                  role="switch"
                  :aria-checked="instance.control_enabled"
                  :aria-label="`${instance.runtime_name || instance.name} 服务控制授权`"
                  :disabled="
                    !controlAuthorizationAvailable(instance) ||
                    controlAuthorizationSavingIds.has(instance.id)
                  "
                  @click="toggleControlAuthorization(instance)"
                >
                  <span aria-hidden="true" />
                </button>
              </div>
              <div v-if="instance.server_id" class="file-access-panel">
                <div class="file-access-heading">
                  <div>
                    <strong>服务器文件授权</strong>
                    <span>读取可授权目录；写入必须精确到文件</span>
                  </div>
                </div>
                <ul v-if="fileAccessesForInstance(instance.id).length" class="file-access-list">
                  <li v-for="access in fileAccessesForInstance(instance.id)" :key="access.id">
                    <span class="file-access-level" :class="access.access_level">
                      {{ access.access_level === 'read' ? '只读' : '可写' }}
                    </span>
                    <code :title="access.path">{{ access.path }}</code>
                    <button type="button" @click="removeFileAccess(access)">移除</button>
                  </li>
                </ul>
                <div class="file-access-form">
                  <select v-model="fileAccessDraft(instance.id).access_level" aria-label="文件访问权限">
                    <option value="read">可读目录或文件</option>
                    <option value="write">精确可写文件</option>
                  </select>
                  <input
                    v-model="fileAccessDraft(instance.id).path"
                    :placeholder="fileAccessDraft(instance.id).access_level === 'read' ? '/opt/my-app/config' : '/etc/my-app/app.conf'"
                    aria-label="目标服务器绝对路径"
                    @keyup.enter="addFileAccess(instance)"
                  />
                  <button
                    type="button"
                    class="btn-secondary"
                    :disabled="fileAccessSavingIds.has(instance.id)"
                    @click="addFileAccess(instance)"
                  >授权</button>
                </div>
              </div>
              <div class="runtime-actions">
                <button type="button" class="btn-secondary" @click="refreshSelectedStatuses">
                  检测状态
                </button>
                <button
                  type="button"
                  class="btn-secondary action-start"
                  :disabled="!runtimeActionAvailable(instance)"
                  :title="
                    runtimeActionAvailable(instance)
                      ? '进入运维对话，发送后仍需人工审批'
                      : `请先为该 ${instance.runtime_type || '运行时'} 实例开启服务控制授权`
                  "
                  @click="requestRuntimeAction(instance, 'start')"
                >启动服务</button>
                <button
                  type="button"
                  class="btn-secondary action-stop"
                  :disabled="!runtimeActionAvailable(instance)"
                  :title="
                    runtimeActionAvailable(instance)
                      ? '进入运维对话，发送后仍需人工审批'
                      : `请先为该 ${instance.runtime_type || '运行时'} 实例开启服务控制授权`
                  "
                  @click="requestRuntimeAction(instance, 'stop')"
                >停止服务</button>
              </div>
            </article>
          </div>
        </section>

      </main>

      <main v-else-if="selectedServer" class="service-detail server-detail">
        <div class="detail-header">
          <div>
            <div class="detail-title">
              <h2>{{ selectedServer.name }}</h2>
              <span class="server-address-badge">{{ selectedServer.ip_address }}</span>
              <span class="server-state" :class="`status-${selectedServer.status}`">
                {{ serverStatusLabel(selectedServer) }}
              </span>
            </div>
            <p>{{ selectedServer.description || '服务器资源与已登记服务运行概览' }}</p>
          </div>
          <button type="button" class="btn-secondary" @click="refreshSelectedServerOverview">
            刷新服务器数据
          </button>
        </div>

        <section class="server-summary-grid" aria-label="服务器服务状态摘要">
          <article class="server-summary-card">
            <span>已登记服务</span>
            <strong>{{ selectedServerServices.length }}</strong>
          </article>
          <article class="server-summary-card">
            <span>部署实例</span>
            <strong>{{ selectedServerInstances.length }}</strong>
          </article>
          <article class="server-summary-card summary-running">
            <span>运行中</span>
            <strong>{{ serverRuntimeCount('running') }}</strong>
          </article>
          <article class="server-summary-card summary-error">
            <span>异常</span>
            <strong>{{ serverRuntimeCount('error') }}</strong>
          </article>
          <article class="server-summary-card">
            <span>已停止</span>
            <strong>{{ serverRuntimeCount('stopped') }}</strong>
          </article>
        </section>

        <section class="detail-section server-metrics-section">
          <div class="section-heading observability-heading">
            <div>
              <h3>服务器关键指标</h3>
              <p>
                来源：{{ selectedServerObservability?.metric_instance || '正在匹配 node_exporter' }}
                <template v-if="selectedServerObservability">
                  · 更新于 {{ formatLogTime(selectedServerObservability.checked_at) }}
                </template>
              </p>
            </div>
            <div class="observability-controls">
              <select v-model.number="serverObservabilityMinutes" aria-label="服务器指标时间范围">
                <option :value="60">最近 1 小时</option>
                <option :value="1440">最近 24 小时</option>
                <option :value="10080">最近 7 天</option>
              </select>
              <a
                v-if="selectedServerObservability?.grafana_url"
                class="btn-secondary grafana-link"
                :href="selectedServerObservability.grafana_url"
                target="_blank"
                rel="noopener noreferrer"
              >在 Grafana 中查看</a>
              <button
                type="button"
                class="btn-secondary"
                @click="refreshServerObservability(selectedServer.id)"
              >刷新指标</button>
            </div>
          </div>

          <p v-if="serverObservabilityLoadingIds.has(selectedServer.id)" class="hint compact">
            正在读取 Prometheus 服务器指标…
          </p>
          <p v-else-if="serverObservabilityErrors[selectedServer.id]" class="error compact">
            {{ serverObservabilityErrors[selectedServer.id] }}
          </p>
          <template v-else-if="selectedServerObservability">
            <div v-if="selectedServerObservability.metrics.length" class="server-metric-grid">
              <article
                v-for="metric in selectedServerObservability.metrics"
                :key="metric.key"
                class="server-metric-card"
                :class="{ bad: metricIsBad(metric) }"
              >
                <span>{{ metric.label }}</span>
                <strong>{{ formatMetric(metric) }}</strong>
              </article>
            </div>
            <p v-else class="data-empty">
              {{ selectedServerObservability.metric_message }}
            </p>

            <div class="server-trend-heading">
              <h4>历史趋势</h4>
              <span>Prometheus 范围查询</span>
            </div>
            <div v-if="selectedServerObservability.series.length" class="server-trend-grid">
              <MetricTrendChart
                v-for="series in selectedServerObservability.series"
                :key="series.key"
                :chart-id="`${selectedServer.id}-${series.key}`"
                :label="series.label"
                :unit="series.unit"
                :points="series.points"
              />
            </div>
            <p v-else class="data-empty">
              {{ selectedServerObservability.series_message }}
            </p>
          </template>
        </section>

        <section class="detail-section">
          <div class="section-heading">
            <div>
              <h3>该服务器上的服务</h3>
              <p>状态来自目标服务器实时检测，点击服务进入服务级指标与日志详情</p>
            </div>
          </div>
          <p v-if="selectedServerServices.length === 0" class="section-empty">
            该服务器还没有登记服务。
          </p>
          <div v-else class="server-service-list">
            <button
              v-for="service in selectedServerServices"
              :key="service.id"
              type="button"
              class="server-service-row"
              @click="openServiceForServer(service.id, selectedServer.id)"
            >
              <span class="server-service-identity">
                <strong>{{ service.name }}</strong>
                <small>{{ service.environment }}</small>
              </span>
              <span>{{ serviceInstanceCountOnServer(service.id, selectedServer.id) }} 个实例</span>
              <span class="server-service-ports">{{ serverInstancePorts(service.id) }}</span>
              <span
                class="runtime-status prominent"
                :class="`runtime-${serviceRuntimeStatus(service.id, selectedServer.id)}`"
              >
                <span class="status-light" aria-hidden="true" />
                {{ serviceStatusLabel(service.id, selectedServer.id) }}
              </span>
            </button>
          </div>
        </section>
      </main>

      <main v-else class="service-placeholder">
        <div>
          <h2>从左侧菜单选择服务器或服务</h2>
          <p>点击服务器查看主机指标与服务概览，点击二级服务查看服务指标和日志。</p>
          <button v-if="services.length === 0" type="button" class="btn-primary" @click="openCreateService">
            新增第一个服务
          </button>
        </div>
      </main>
    </div>

    <div v-if="serviceModal" class="modal-backdrop" @click.self="serviceModal = false">
      <div class="modal" role="dialog" aria-modal="true" aria-label="服务表单">
        <div class="modal-header">
          <h2>{{ editingService ? '编辑服务' : '新增服务' }}</h2>
          <button type="button" class="close-button" @click="serviceModal = false">×</button>
        </div>
        <form class="modal-body" @submit.prevent="submitService">
          <div class="form-row">
            <label>服务名 *<input v-model="serviceForm.name" placeholder="例如 orders-api" /></label>
            <label>环境 *<input v-model="serviceForm.environment" placeholder="例如 prod" /></label>
          </div>
          <label>服务说明<textarea v-model="serviceForm.description" rows="3" /></label>
          <p v-if="formError" class="error">{{ formError }}</p>
          <div class="modal-actions">
            <button type="button" class="btn-ghost" @click="serviceModal = false">取消</button>
            <button type="submit" class="btn-primary" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
          </div>
        </form>
      </div>
    </div>

    <div v-if="instanceModal" class="modal-backdrop" @click.self="instanceModal = false">
      <div class="modal" role="dialog" aria-modal="true" aria-label="服务实例表单">
        <div class="modal-header">
          <h2>{{ editingInstance ? '编辑部署实例' : '添加部署实例' }}</h2>
          <button type="button" class="close-button" @click="instanceModal = false">×</button>
        </div>
        <form class="modal-body" @submit.prevent="submitInstance">
          <label>实例名称 *<input v-model="instanceForm.name" placeholder="例如 orders-api-01" /></label>
          <label>
            关联服务器
            <select v-model="instanceForm.server_id">
              <option value="">不绑定服务器</option>
              <option v-for="server in servers" :key="server.id" :value="server.id">
                {{ server.name }}（{{ server.ip_address }}）
              </option>
            </select>
          </label>
          <div class="form-row">
            <label>
              运行类型
              <select v-model="instanceForm.runtime_type">
                <option value="">未配置</option>
                <option value="systemd">systemd</option>
                <option value="docker">Docker</option>
                <option value="process">未纳管进程</option>
              </select>
            </label>
            <label>
              运行对象
              <input
                v-model="instanceForm.runtime_name"
                placeholder="例如 orders-api.service"
              />
            </label>
          </div>
          <div class="form-row">
            <label>Prometheus instance（可选）<input v-model="instanceForm.metric_instance" placeholder="host:9100" /></label>
            <label>Loki 服务标签<input v-model="instanceForm.log_service" placeholder="orders-api" /></label>
          </div>
          <label>实例说明<textarea v-model="instanceForm.description" rows="3" /></label>
          <p v-if="formError" class="error">{{ formError }}</p>
          <div class="modal-actions">
            <button type="button" class="btn-ghost" @click="instanceModal = false">取消</button>
            <button type="submit" class="btn-primary" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
          </div>
        </form>
      </div>
    </div>

  </div>
</template>

<style scoped>
.services-view {
  display: flex;
  height: 100%;
  min-height: 0;
  width: 100%;
  max-width: 1600px;
  flex-direction: column;
  margin: 0 auto;
  overflow: hidden;
  padding: 2rem;
}

.view-header,
.detail-header,
.detail-title,
.header-actions,
.section-heading,
.card-heading,
.inline-actions,
.modal-header,
.modal-actions {
  display: flex;
  align-items: center;
}

.view-header {
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1.35rem;
}

h1,
h2,
h3,
h4,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 0;
  font-size: 1.5rem;
}

.subtitle {
  margin: 0.25rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.9rem;
}

.btn-primary,
.btn-ghost,
.btn-secondary {
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.85rem;
  font-size: 0.85rem;
  white-space: nowrap;
}

.btn-primary {
  border: 1px solid var(--color-accent);
  color: var(--color-accent-contrast);
  background: var(--color-accent);
}

.btn-primary:hover {
  background: var(--color-accent-hover);
}

.btn-ghost,
.btn-secondary {
  border: 1px solid var(--color-border);
  color: var(--color-text);
  background: var(--color-surface);
}

.btn-ghost:hover,
.btn-secondary:hover {
  background: var(--color-bg);
}

.grafana-link {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.danger {
  color: #b42318 !important;
}

button:disabled {
  cursor: default;
  opacity: 0.55;
}

.error {
  padding: 0.65rem 0.8rem;
  border-radius: var(--radius-sm);
  color: #b42318;
  background: #fef3f2;
}

.hint,
.list-empty,
.section-empty {
  color: var(--color-text-muted);
}

.directory-layout {
  display: grid;
  flex: 1;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 1rem;
  align-items: stretch;
  min-height: 0;
}

.directory-menu-panel,
.service-detail,
.service-placeholder {
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
  box-shadow: var(--shadow-sm);
}

.directory-menu-panel {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  gap: 0.65rem;
  overflow-y: auto;
  padding: 0.75rem;
  scrollbar-gutter: stable;
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.55rem;
  padding: 0.15rem 0.15rem 0.45rem;
}

.panel-heading h2 {
  margin: 0;
  font-size: 0.92rem;
}

.panel-count {
  min-width: 1.65rem;
  border-radius: 999px;
  padding: 0.15rem 0.4rem;
  color: var(--color-text-muted);
  background: var(--color-bg);
  font-size: 0.7rem;
  text-align: center;
}

.directory-search {
  width: 100%;
}

.server-tree {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.server-menu-group {
  overflow: visible;
}

.server-menu-item {
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 0.7rem 0.65rem;
  color: var(--color-text);
  background: transparent;
  text-align: left;
}

.server-menu-item:hover {
  background: transparent;
  color: var(--color-accent);
}

.server-menu-item.active {
  color: var(--color-accent);
  background: transparent;
}

.server-menu-item:focus-visible,
.service-submenu-item:focus-visible,
.server-service-row:focus-visible {
  outline: none;
  color: var(--color-accent);
}

.menu-chevron {
  color: var(--color-text-muted);
  font-size: 1.1rem;
  line-height: 1;
  transition: transform 0.15s ease;
}

.menu-chevron.expanded {
  transform: rotate(90deg);
}

.server-menu-main,
.server-menu-side {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.server-menu-main {
  gap: 0.12rem;
}

.server-menu-side {
  align-items: flex-end;
  gap: 0.18rem;
  color: var(--color-text-muted);
  font-size: 0.62rem;
}

.server-menu-name,
.server-menu-address {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.server-menu-name {
  font-size: 0.82rem;
  font-weight: 650;
}

.server-menu-address {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.server-state {
  border-radius: 999px;
  padding: 0.1rem 0.35rem;
  color: var(--color-text-muted);
  background: var(--color-bg);
  font-size: 0.6rem;
  white-space: nowrap;
}

.server-state.status-active {
  color: #067647;
  background: #ecfdf3;
}

.server-state.status-unreachable {
  color: #b42318;
  background: #fef3f2;
}

.server-state.status-unconfigured {
  color: #b54708;
  background: #fffaeb;
}

.list-empty {
  padding: 1rem 0.5rem;
  font-size: 0.82rem;
  text-align: center;
}

.service-submenu {
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
  margin: 0 0.4rem 0.35rem 1.1rem;
  padding: 0.2rem 0 0.15rem 0.65rem;
  border-left: 1px solid var(--color-border);
}

.submenu-empty {
  margin: 0;
  padding: 0.55rem 0.6rem;
  color: var(--color-text-muted);
  font-size: 0.7rem;
}

.service-submenu-item {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  width: 100%;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.65rem;
  color: var(--color-text);
  background: transparent;
  text-align: left;
}

.service-submenu-item:hover {
  background: transparent;
  color: var(--color-accent);
}

.service-submenu-item.active {
  border-color: transparent;
  background: transparent;
  color: var(--color-accent);
}

.submenu-service-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
}

.service-name {
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.environment-badge {
  border-radius: 999px;
  padding: 0.12rem 0.45rem;
  color: #3730a3;
  background: #eef2ff;
  font-size: 0.68rem;
  font-weight: 600;
}

.server-address-badge {
  border-radius: 999px;
  padding: 0.16rem 0.5rem;
  color: var(--color-text-muted);
  background: var(--color-bg);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.68rem;
}

.runtime-status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  flex: none;
  color: var(--color-text-muted);
  font-size: 0.68rem;
  font-weight: 600;
  white-space: nowrap;
}

.status-light {
  width: 0.55rem;
  height: 0.55rem;
  border: 2px solid color-mix(in srgb, #98a2b3 35%, transparent);
  border-radius: 50%;
  background: #98a2b3;
  box-shadow: 0 0 0 2px color-mix(in srgb, #98a2b3 14%, transparent);
}

.runtime-running {
  color: #067647;
}

.runtime-running .status-light {
  border-color: color-mix(in srgb, #12b76a 35%, transparent);
  background: #12b76a;
  box-shadow: 0 0 0 2px color-mix(in srgb, #12b76a 14%, transparent);
}

.runtime-error {
  color: #b42318;
}

.runtime-error .status-light {
  border-color: color-mix(in srgb, #f04438 35%, transparent);
  background: #f04438;
  box-shadow: 0 0 0 2px color-mix(in srgb, #f04438 14%, transparent);
}

.runtime-stopped .status-light,
.runtime-unknown .status-light {
  background: #98a2b3;
}

.runtime-status.prominent {
  border: 1px solid currentcolor;
  border-radius: 999px;
  padding: 0.25rem 0.55rem;
}

.service-counts {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.service-detail {
  grid-column: auto / -1;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 1.25rem;
  scrollbar-gutter: stable;
}

.server-summary-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.65rem;
  margin-top: 1.25rem;
}

.server-summary-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.75rem;
  background: var(--color-bg);
}

.server-summary-card span {
  color: var(--color-text-muted);
  font-size: 0.7rem;
}

.server-summary-card strong {
  font-size: 1.25rem;
}

.server-summary-card.summary-running strong {
  color: #067647;
}

.server-summary-card.summary-error strong {
  color: #b42318;
}

.detail-header {
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.detail-title {
  flex-wrap: wrap;
  gap: 0.55rem;
}

.detail-title h2 {
  margin: 0;
  font-size: 1.25rem;
}

.detail-header p {
  margin: 0.4rem 0 0;
  color: var(--color-text-muted);
  overflow-wrap: anywhere;
}

.header-actions,
.inline-actions {
  gap: 0.4rem;
}

.detail-section {
  margin-top: 1.4rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--color-border);
}

.observability-section {
  border-top: 0;
}

.observability-heading {
  align-items: center;
}

.observability-controls,
.runtime-actions,
.observability-instance-head,
.panel-subheading,
.instance-title {
  display: flex;
  align-items: center;
}

.observability-controls {
  gap: 0.45rem;
}

.control-authorization {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 0.85rem;
  border-radius: var(--radius-sm);
  padding: 0.7rem 0.8rem;
  background: #f8f9fa;
}

.control-authorization > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.18rem;
}

.control-authorization strong {
  font-size: 0.76rem;
}

.control-authorization span {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.file-access-panel {
  margin-top: 0.7rem;
  border-radius: var(--radius-sm);
  padding: 0.7rem 0.8rem;
  background: #f8f9fa;
}

.file-access-heading > div {
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
}

.file-access-heading strong {
  font-size: 0.76rem;
}

.file-access-heading span {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.file-access-list {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin: 0.65rem 0 0;
  padding: 0;
  list-style: none;
}

.file-access-list li {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.45rem;
}

.file-access-list code {
  overflow: hidden;
  font-size: 0.68rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-access-list button {
  border: 0;
  padding: 0;
  color: #b42318;
  background: transparent;
  font-size: 0.68rem;
}

.file-access-level {
  border-radius: 4px;
  padding: 0.12rem 0.35rem;
  color: #175cd3;
  background: #e7effe;
  font-size: 0.62rem;
}

.file-access-level.write {
  color: #b54708;
  background: #fffaeb;
}

.file-access-form {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr) auto;
  gap: 0.4rem;
  margin-top: 0.65rem;
}

.file-access-form select,
.file-access-form input {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.38rem 0.45rem;
  background: #fff;
  font-size: 0.68rem;
}

.file-access-form .btn-secondary {
  padding: 0.38rem 0.6rem;
  font-size: 0.68rem;
}

.control-switch {
  position: relative;
  flex: 0 0 auto;
  width: 42px;
  height: 24px;
  border: 0;
  border-radius: 999px;
  padding: 0;
  background: #cbd2dc;
  cursor: pointer;
  transition: background 0.18s ease;
}

.control-switch > span {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgb(16 24 40 / 22%);
  transition: transform 0.18s ease;
}

.control-switch.active {
  background: #3157c8;
}

.control-switch.active > span {
  transform: translateX(18px);
}

.control-switch:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.observability-controls select {
  width: auto;
  min-width: 120px;
  padding: 0.48rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  background: var(--color-surface);
}

.observability-stack {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

.observability-card {
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-bg);
}

.observability-instance-head {
  justify-content: space-between;
  gap: 1rem;
  padding: 0.75rem 0.9rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}

.observability-instance-head > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.12rem;
}

.observability-instance-head strong {
  overflow: hidden;
  font-size: 0.84rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.observability-instance-head > div span,
.panel-subheading span {
  color: var(--color-text-muted);
  font-size: 0.68rem;
  overflow-wrap: anywhere;
}

.observability-grid {
  display: grid;
  grid-template-columns: minmax(250px, 0.75fr) minmax(360px, 1.6fr);
  gap: 0;
}

.metric-panel,
.log-panel {
  min-width: 0;
  padding: 0.9rem;
}

.metric-panel {
  border-right: 1px solid var(--color-border);
}

.panel-subheading {
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.65rem;
}

.panel-subheading h4 {
  margin: 0;
  font-size: 0.78rem;
}

.panel-subheading span {
  max-width: 65%;
  text-align: right;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.5rem;
}

.metric-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.2rem;
  border: 1px solid #d1fadf;
  border-radius: var(--radius-sm);
  padding: 0.65rem;
  background: #f6fef9;
}

.metric-card.bad {
  border-color: #fecdca;
  background: #fef3f2;
}

.metric-card span {
  color: var(--color-text-muted);
  font-size: 0.66rem;
}

.metric-card strong {
  color: #067647;
  font-size: 1rem;
}

.metric-card.bad strong {
  color: #b42318;
}

.server-metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.65rem;
}

.server-metric-card {
  display: flex;
  min-width: 0;
  min-height: 92px;
  flex-direction: column;
  justify-content: space-between;
  gap: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.8rem;
  background: var(--color-bg);
}

.server-metric-card span {
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.server-metric-card strong {
  color: var(--color-text);
  font-size: 1.25rem;
}

.server-metric-card.bad {
  border-color: #fecdca;
  background: #fef3f2;
}

.server-metric-card.bad strong {
  color: #b42318;
}

.server-trend-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin: 1.1rem 0 0.65rem;
}

.server-trend-heading h4 {
  margin: 0;
  font-size: 0.82rem;
}

.server-trend-heading span {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.server-trend-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.65rem;
}

.server-service-list {
  overflow: visible;
}

.server-service-row {
  display: grid;
  grid-template-columns: minmax(180px, 1.2fr) 90px minmax(180px, 1fr) auto;
  gap: 0.8rem;
  align-items: center;
  width: 100%;
  border: 0;
  border-bottom: 0;
  padding: 0.75rem 0.85rem;
  color: var(--color-text);
  background: transparent;
  text-align: left;
}

.server-service-row:last-child {
  border-bottom: 0;
}

.server-service-row:hover {
  background: transparent;
  color: var(--color-accent);
}

.server-service-identity {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.15rem;
}

.server-service-identity strong {
  overflow: hidden;
  font-size: 0.82rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.server-service-identity small,
.server-service-row > span:not(.server-service-identity) {
  color: var(--color-text-muted);
  font-size: 0.7rem;
}

.server-service-ports {
  overflow-wrap: anywhere;
}

.log-list {
  display: flex;
  max-height: 310px;
  flex-direction: column;
  gap: 0.15rem;
  overflow-y: auto;
}

.log-row {
  display: grid;
  grid-template-columns: 86px 42px minmax(0, 1fr);
  gap: 0.45rem;
  align-items: start;
  padding: 0.35rem 0.4rem;
  border-radius: 4px;
  font-size: 0.68rem;
}

.log-row:hover {
  background: var(--color-surface);
}

.log-row time {
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

.log-row code {
  min-width: 0;
  color: var(--color-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  line-height: 1.35;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.log-level {
  border-radius: 999px;
  color: var(--color-text-muted);
  background: #eaecf0;
  font-size: 0.58rem;
  font-weight: 700;
  text-align: center;
  text-transform: uppercase;
}

.log-level.log-error {
  color: #b42318;
  background: #fee4e2;
}

.log-level.log-warn {
  color: #b54708;
  background: #fef0c7;
}

.log-level.log-info {
  color: #175cd3;
  background: #eff8ff;
}

.data-empty,
.hint.compact,
.error.compact {
  margin: 0;
  padding: 0.75rem;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  background: var(--color-surface);
  font-size: 0.72rem;
}

.hint.compact,
.error.compact {
  margin: 0.8rem;
}

.error.compact {
  color: #b42318;
  background: #fef3f2;
}

.section-heading {
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.8rem;
}

.section-heading h3 {
  margin-bottom: 0;
  font-size: 0.98rem;
}

.section-heading p {
  margin: 0.15rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.section-empty {
  margin: 0;
  padding: 0.75rem;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 0.78rem;
  text-align: center;
}

.instance-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.instance-card {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0.8rem;
  background: var(--color-bg);
}

.card-heading {
  justify-content: space-between;
  gap: 0.5rem;
}

.card-heading h4 {
  overflow: hidden;
  margin: 0;
  font-size: 0.88rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.instance-title {
  min-width: 0;
  gap: 0.5rem;
}

.runtime-actions {
  gap: 0.45rem;
  margin-top: 0.75rem;
  padding-top: 0.7rem;
  border-top: 1px solid var(--color-border);
}

.runtime-actions .btn-secondary {
  flex: 1;
  padding: 0.4rem 0.5rem;
  font-size: 0.72rem;
}

.runtime-actions .action-start {
  color: #067647;
  border-color: #abefc6;
}

.runtime-actions .action-stop {
  color: #b42318;
  border-color: #fecdca;
}

.inline-actions button {
  border: 0;
  padding: 0;
  color: var(--color-accent);
  background: transparent;
  font-size: 0.72rem;
}

.instance-card > p {
  margin: 0.4rem 0;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}

.instance-card dl {
  display: grid;
  gap: 0.25rem;
  margin: 0;
}

.instance-card dl div {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 0.4rem;
}

.instance-card dt,
.instance-card dd {
  font-size: 0.72rem;
  overflow-wrap: anywhere;
}

.instance-card dt {
  color: var(--color-text-muted);
}

.instance-card dd {
  margin: 0;
}

.service-placeholder {
  grid-column: auto / -1;
  display: grid;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  place-items: center;
  padding: 2rem;
  color: var(--color-text-muted);
  text-align: center;
}

.service-placeholder h2 {
  margin-bottom: 0.4rem;
  color: var(--color-text);
  font-size: 1.1rem;
}

.modal-backdrop {
  position: fixed;
  z-index: 30;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1rem;
  background: rgba(24, 24, 27, 0.3);
}

.modal {
  width: min(600px, 96vw);
  max-height: 92vh;
  overflow-y: auto;
  border-radius: var(--radius);
  background: var(--color-surface);
  box-shadow: var(--shadow-lg);
}

.modal-header {
  justify-content: space-between;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.modal-header h2 {
  margin: 0;
  font-size: 1.05rem;
}

.close-button {
  border: 0;
  color: var(--color-text-muted);
  background: transparent;
  font-size: 1.4rem;
}

.modal-body {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1.25rem;
}

.modal-body label {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0.3rem;
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.form-row {
  display: flex;
  gap: 0.9rem;
}

.modal-body input,
.modal-body select,
.modal-body textarea {
  width: 100%;
}

.modal-actions {
  justify-content: flex-end;
  gap: 0.5rem;
  padding-top: 0.25rem;
}

@media (max-width: 980px) {
  .services-view {
    height: auto;
    min-height: 100%;
    overflow: visible;
  }

  .directory-layout {
    flex: none;
    grid-template-columns: 1fr;
    min-height: auto;
  }

  .directory-menu-panel {
    height: auto;
    max-height: none;
    overflow: visible;
  }

  .service-detail,
  .service-placeholder {
    grid-column: auto;
    height: auto;
    overflow: visible;
  }

  .observability-grid {
    grid-template-columns: 1fr;
  }

  .metric-panel {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .server-summary-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .server-service-row {
    grid-template-columns: minmax(180px, 1fr) auto auto;
  }

  .server-service-ports {
    display: none;
  }
}

@media (max-width: 700px) {
  .services-view {
    padding: 1.25rem;
  }

  .instance-grid {
    grid-template-columns: 1fr;
  }

  .server-summary-grid,
  .server-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .server-trend-grid {
    grid-template-columns: 1fr;
  }

  .server-service-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .server-service-row > span:nth-child(2) {
    display: none;
  }

  .form-row {
    flex-direction: column;
  }

  .detail-header {
    flex-direction: column;
  }

  .observability-heading,
  .observability-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .log-row {
    grid-template-columns: 76px 38px minmax(0, 1fr);
  }
}

.services-view {
  padding: 2.25rem clamp(1.25rem, 3vw, 2.75rem) 2rem;
}

.view-header {
  align-items: flex-end;
  margin-bottom: 1.25rem;
  padding-bottom: 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.view-kicker {
  display: block;
  margin-bottom: 0.18rem;
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.62rem;
  font-weight: 600;
  letter-spacing: 0.12em;
}

h1 {
  font-family: var(--font-display);
  font-size: 1.82rem;
  font-weight: 650;
  letter-spacing: 0.005em;
}

.subtitle {
  margin-top: 0.32rem;
  font-size: 0.84rem;
}

.btn-primary,
.btn-secondary,
.btn-ghost {
  min-height: 38px;
  border-radius: 7px;
}

.btn-primary {
  box-shadow: 0 5px 14px rgba(22, 137, 154, 0.15);
}

.directory-layout {
  gap: 12px;
}

.directory-menu-panel {
  border-color: var(--color-border);
  border-radius: 10px;
  background: #f1f5f6;
  box-shadow: none;
}

.directory-menu-panel > .panel-heading h2 {
  font-family: var(--font-display);
  letter-spacing: 0.01em;
}

.directory-search {
  background: rgba(255, 255, 255, 0.78);
}

.server-menu-item,
.service-submenu-item {
  border-radius: 7px;
}

.server-menu-item.active,
.service-submenu-item.active {
  border-color: #c5dfe3;
  background: var(--color-interactive-bg);
}

.service-detail,
.service-placeholder {
  border-color: var(--color-border);
  border-radius: 10px;
  background: var(--color-surface-strong);
  box-shadow: none;
}

.detail-header h2,
.section-heading h3,
.card-heading h3 {
  font-family: var(--font-display);
  letter-spacing: 0.01em;
}

.server-summary-grid,
.server-metric-grid,
.instance-grid {
  gap: 10px;
}

.server-summary-card,
.server-metric-card,
.instance-card,
.detail-section {
  border-color: var(--color-border);
  border-radius: 9px;
  box-shadow: none;
}

.server-summary-card strong,
.server-metric-card strong,
.metric-value {
  font-family: var(--font-display);
  font-weight: 550;
}

.runtime-status,
.server-state,
.panel-count,
.server-address-badge {
  font-family: var(--font-data);
  font-size: 0.65rem;
}

.log-row,
.rule-expression,
.server-menu-address,
.server-service-ports {
  font-family: var(--font-data);
}

.modal-backdrop {
  background: rgba(8, 28, 38, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  border-radius: 14px;
}

@media (max-width: 700px) {
  .services-view {
    padding: 1.4rem 1rem 1.2rem;
  }
}
</style>
