<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  createService,
  createServiceDependency,
  createServiceInstance,
  deleteService,
  deleteServiceDependency,
  deleteServiceInstance,
  fetchServiceDependencies,
  fetchServiceInstanceStatuses,
  fetchServiceInstances,
  fetchServiceObservability,
  fetchServices,
  fetchServiceTopology,
  updateService,
  updateServiceDependency,
  updateServiceInstance,
} from '../api/services'
import { fetchServers } from '../api/servers'
import ServiceTopology from '../components/ServiceTopology.vue'
import type { Server } from '../types/server'
import type {
  DependencyType,
  ServiceDependency,
  ServiceDependencyInput,
  ServiceInput,
  ServiceInstance,
  ServiceInstanceInput,
  ServiceRecord,
  ServiceTopology as ServiceTopologyData,
  RuntimeType,
  RuntimeStatus,
  ServiceInstanceStatus,
  ServiceMetric,
  ServiceObservability,
} from '../types/service'

const route = useRoute()
const router = useRouter()
const services = ref<ServiceRecord[]>([])
const instances = ref<ServiceInstance[]>([])
const dependencies = ref<ServiceDependency[]>([])
const servers = ref<Server[]>([])
const topology = ref<ServiceTopologyData | null>(null)
const loading = ref(true)
const topologyLoading = ref(false)
const error = ref<string | null>(null)
const formError = ref<string | null>(null)
const saving = ref(false)
const search = ref('')
const selectedServerId = ref<string | null>(null)
const expandedServerIds = ref<Set<string>>(new Set())
const instanceStatuses = ref<Record<string, ServiceInstanceStatus>>({})
const statusLoadingServerIds = ref<Set<string>>(new Set())
const observabilityByInstance = ref<Record<string, ServiceObservability>>({})
const observabilityLoadingIds = ref<Set<string>>(new Set())
const observabilityErrors = ref<Record<string, string>>({})
const observabilityMinutes = ref(1440)

const serviceModal = ref(false)
const editingService = ref<ServiceRecord | null>(null)
const serviceForm = reactive({
  name: '',
  environment: 'prod',
  owner: '',
  description: '',
  repository_url: '',
  dashboard_url: '',
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

const dependencyModal = ref(false)
const editingDependency = ref<ServiceDependency | null>(null)
const dependencyForm = reactive<{
  direction: 'depends_on' | 'depended_by'
  target_service_id: string
  dependency_type: DependencyType
  description: string
}>({
  direction: 'depends_on',
  target_service_id: '',
  dependency_type: 'service',
  description: '',
})

const dependencyTypes: { value: DependencyType; label: string }[] = [
  { value: 'service', label: '内部服务' },
  { value: 'database', label: '数据库' },
  { value: 'cache', label: '缓存' },
  { value: 'queue', label: '消息队列' },
  { value: 'external', label: '外部服务' },
]
const dependencyTypeLabel = Object.fromEntries(
  dependencyTypes.map((item) => [item.value, item.label]),
) as Record<DependencyType, string>

const selectedId = computed(() => {
  const value = route.params.serviceId
  return typeof value === 'string' ? value : null
})
const selectedService = computed(
  () => services.value.find((service) => service.id === selectedId.value) ?? null,
)
const serviceMap = computed(
  () => new Map(services.value.map((service) => [service.id, service])),
)
const serverMap = computed(() => new Map(servers.value.map((server) => [server.id, server])))
const selectedServer = computed(
  () => servers.value.find((server) => server.id === selectedServerId.value) ?? null,
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
const directDependencies = computed(() =>
  dependencies.value.filter((edge) => edge.upstream_service_id === selectedId.value),
)
const directDependents = computed(() =>
  dependencies.value.filter((edge) => edge.downstream_service_id === selectedId.value),
)
const targetServices = computed(() =>
  services.value.filter((service) => service.id !== selectedId.value),
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
  return [service.name, service.environment, service.owner ?? ''].some((value) =>
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
  if (statusLoadingServerIds.value.has(serverId)) return '检测中'
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
  if (instance.server_id && statusLoadingServerIds.value.has(instance.server_id)) return '检测中'
  return instanceStatus(instance)?.label ?? '未检测'
}

function instanceStatusClass(instance: ServiceInstance): RuntimeStatus {
  return instanceStatus(instance)?.status ?? 'unknown'
}

function setStatusLoading(serverId: string, loadingState: boolean) {
  const next = new Set(statusLoadingServerIds.value)
  if (loadingState) next.add(serverId)
  else next.delete(serverId)
  statusLoadingServerIds.value = next
}

async function refreshStatuses(filters: { serverId?: string; serviceId?: string }) {
  if (filters.serverId) setStatusLoading(filters.serverId, true)
  try {
    const rows = await fetchServiceInstanceStatuses(filters)
    for (const row of rows) instanceStatuses.value[row.instance_id] = row
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (filters.serverId) setStatusLoading(filters.serverId, false)
  }
}

async function refreshServerStatuses(serverId: string) {
  await refreshStatuses({ serverId })
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
      (instance.runtime_type === 'systemd' || instance.runtime_type === 'docker'),
  )
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

function dependencyCount(serviceId: string): number {
  return dependencies.value.filter(
    (edge) =>
      edge.upstream_service_id === serviceId || edge.downstream_service_id === serviceId,
  ).length
}

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const [serviceRows, instanceRows, dependencyRows, serverRows] = await Promise.all([
      fetchServices(),
      fetchServiceInstances(),
      fetchServiceDependencies(),
      fetchServers(),
    ])
    services.value = serviceRows
    instances.value = instanceRows
    dependencies.value = dependencyRows
    servers.value = serverRows
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

async function loadTopology() {
  const id = selectedId.value
  topology.value = null
  if (!id) return
  topologyLoading.value = true
  try {
    topology.value = await fetchServiceTopology(id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    topologyLoading.value = false
  }
}

onMounted(loadAll)
watch(
  selectedId,
  (serviceId) => {
    void loadTopology()
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
  if (selectedServerId.value) expandServer(selectedServerId.value)
}

function openService(serviceId: string) {
  syncSelectedServerToService(serviceId)
  void router.push(`/services/${serviceId}`)
}

function openServiceForServer(serviceId: string, serverId: string) {
  selectedServerId.value = serverId
  expandServer(serverId)
  void router.push(`/services/${serviceId}`)
}

function expandServer(serverId: string) {
  const next = new Set(expandedServerIds.value)
  next.add(serverId)
  expandedServerIds.value = next
  if (
    instances.value.some((instance) => instance.server_id === serverId) &&
    !statusLoadingServerIds.value.has(serverId)
  ) {
    void refreshServerStatuses(serverId)
  }
}

function isServerExpanded(serverId: string): boolean {
  return Boolean(search.value.trim()) || expandedServerIds.value.has(serverId)
}

function toggleServer(serverId: string) {
  const next = new Set(expandedServerIds.value)
  if (next.has(serverId)) {
    next.delete(serverId)
  } else {
    next.add(serverId)
    selectedServerId.value = serverId
    void refreshServerStatuses(serverId)
    if (
      selectedId.value &&
      !instances.value.some(
        (instance) =>
          instance.service_id === selectedId.value && instance.server_id === serverId,
      )
    ) {
      void router.push('/services')
    }
  }
  expandedServerIds.value = next
}

function resetServiceForm() {
  editingService.value = null
  serviceForm.name = ''
  serviceForm.environment = 'prod'
  serviceForm.owner = ''
  serviceForm.description = ''
  serviceForm.repository_url = ''
  serviceForm.dashboard_url = ''
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
  serviceForm.owner = service.owner ?? ''
  serviceForm.description = service.description ?? ''
  serviceForm.repository_url = service.repository_url ?? ''
  serviceForm.dashboard_url = service.dashboard_url ?? ''
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
    owner: serviceForm.owner.trim() || null,
    description: serviceForm.description.trim() || null,
    repository_url: serviceForm.repository_url.trim() || null,
    dashboard_url: serviceForm.dashboard_url.trim() || null,
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
    await loadTopology()
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function removeService(service: ServiceRecord) {
  if (!confirm(`确定删除服务 ${service.name}/${service.environment}？它的实例和依赖边也会删除。`)) {
    return
  }
  error.value = null
  try {
    await deleteService(service.id)
    services.value = services.value.filter((item) => item.id !== service.id)
    instances.value = instances.value.filter((item) => item.service_id !== service.id)
    dependencies.value = dependencies.value.filter(
      (edge) =>
        edge.upstream_service_id !== service.id && edge.downstream_service_id !== service.id,
    )
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
      expandServer(saved.server_id)
    }
    instanceModal.value = false
    await loadTopology()
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

function resetDependencyForm() {
  editingDependency.value = null
  dependencyForm.direction = 'depends_on'
  dependencyForm.target_service_id = targetServices.value[0]?.id ?? ''
  dependencyForm.dependency_type = 'service'
  dependencyForm.description = ''
  formError.value = null
}

function openCreateDependency() {
  resetDependencyForm()
  dependencyModal.value = true
}

function openEditDependency(edge: ServiceDependency) {
  editingDependency.value = edge
  const isOutgoing = edge.upstream_service_id === selectedId.value
  dependencyForm.direction = isOutgoing ? 'depends_on' : 'depended_by'
  dependencyForm.target_service_id = isOutgoing
    ? edge.downstream_service_id
    : edge.upstream_service_id
  dependencyForm.dependency_type = edge.dependency_type
  dependencyForm.description = edge.description ?? ''
  formError.value = null
  dependencyModal.value = true
}

async function submitDependency() {
  if (!selectedService.value || !dependencyForm.target_service_id) {
    formError.value = '请选择关联服务'
    return
  }
  const currentId = selectedService.value.id
  const targetId = dependencyForm.target_service_id
  const payload: ServiceDependencyInput = {
    upstream_service_id:
      dependencyForm.direction === 'depends_on' ? currentId : targetId,
    downstream_service_id:
      dependencyForm.direction === 'depends_on' ? targetId : currentId,
    dependency_type: dependencyForm.dependency_type,
    description: dependencyForm.description.trim() || null,
  }
  saving.value = true
  formError.value = null
  try {
    const saved = editingDependency.value
      ? await updateServiceDependency(editingDependency.value.id, payload)
      : await createServiceDependency(payload)
    const index = dependencies.value.findIndex((edge) => edge.id === saved.id)
    if (index === -1) dependencies.value.push(saved)
    else dependencies.value[index] = saved
    dependencyModal.value = false
    await loadTopology()
  } catch (e) {
    formError.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function removeDependency(edge: ServiceDependency) {
  if (!confirm('确定删除这条服务依赖关系？')) return
  error.value = null
  try {
    await deleteServiceDependency(edge.id)
    dependencies.value = dependencies.value.filter((item) => item.id !== edge.id)
    await loadTopology()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

function serviceLabel(serviceId: string): string {
  const service = serviceMap.value.get(serviceId)
  return service ? `${service.name}/${service.environment}` : '未知服务'
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

function safeExternalUrl(value: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}
</script>

<template>
  <div class="services-view">
    <header class="view-header">
      <div>
        <h1>服务目录</h1>
        <p class="subtitle">按服务器查看服务状态、关键指标、近期日志和依赖关系</p>
      </div>
      <button type="button" class="btn-primary" @click="openCreateService">+ 新增服务</button>
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
                <span class="service-owner">{{ service.owner || '未设置负责人' }}</span>
                <span class="service-counts">
                  {{ serviceInstanceCountOnServer(service.id, server.id) }} 个实例 ·
                  {{ dependencyCount(service.id) }} 条关系
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

        <dl class="service-meta">
          <div><dt>负责人</dt><dd>{{ selectedService.owner || '未设置' }}</dd></div>
          <div>
            <dt>代码仓库</dt>
            <dd>
              <a
                v-if="safeExternalUrl(selectedService.repository_url)"
                :href="safeExternalUrl(selectedService.repository_url) || undefined"
                target="_blank"
                rel="noopener noreferrer"
              >打开仓库</a>
              <span v-else>未设置</span>
            </dd>
          </div>
          <div>
            <dt>仪表盘</dt>
            <dd>
              <a
                v-if="safeExternalUrl(selectedService.dashboard_url)"
                :href="safeExternalUrl(selectedService.dashboard_url) || undefined"
                target="_blank"
                rel="noopener noreferrer"
              >打开仪表盘</a>
              <span v-else>未设置</span>
            </dd>
          </div>
        </dl>

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
                      <span>{{ instance.metric_instance || '按目录标签匹配' }}</span>
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

        <section class="detail-section topology-section">
          <div class="section-heading">
            <div><h3>依赖拓扑</h3><p>调用方 → 被调用方，默认展示三层关联</p></div>
          </div>
          <p v-if="topologyLoading" class="hint">正在生成拓扑…</p>
          <ServiceTopology
            v-else-if="topology"
            :nodes="topology.nodes"
            :edges="topology.edges"
            :focus-id="selectedService.id"
            @select="openService"
          />
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
                <div><dt>Prometheus</dt><dd>{{ instance.metric_instance || '未配置' }}</dd></div>
                <div><dt>Loki</dt><dd>{{ instance.log_service || '未配置' }}</dd></div>
              </dl>
              <p v-if="instance.description" class="instance-description">{{ instance.description }}</p>
              <div class="runtime-actions">
                <button type="button" class="btn-secondary" @click="refreshSelectedStatuses">
                  检测状态
                </button>
                <button
                  type="button"
                  class="btn-secondary action-start"
                  :disabled="!runtimeActionAvailable(instance)"
                  title="进入运维对话，发送后仍需人工审批"
                  @click="requestRuntimeAction(instance, 'start')"
                >启动服务</button>
                <button
                  type="button"
                  class="btn-secondary action-stop"
                  :disabled="!runtimeActionAvailable(instance)"
                  title="进入运维对话，发送后仍需人工审批"
                  @click="requestRuntimeAction(instance, 'stop')"
                >停止服务</button>
              </div>
            </article>
          </div>
        </section>

        <section class="detail-section">
          <div class="section-heading">
            <div><h3>直接依赖</h3><p>维护当前服务依赖谁，以及谁依赖当前服务</p></div>
            <button
              type="button"
              class="btn-secondary"
              :disabled="targetServices.length === 0"
              @click="openCreateDependency"
            >+ 添加依赖</button>
          </div>

          <div class="dependency-columns">
            <div>
              <h4>当前服务依赖</h4>
              <p v-if="directDependencies.length === 0" class="section-empty">无直接依赖</p>
              <article v-for="edge in directDependencies" :key="edge.id" class="dependency-row">
                <div>
                  <strong>{{ serviceLabel(edge.downstream_service_id) }}</strong>
                  <span>{{ dependencyTypeLabel[edge.dependency_type] }}</span>
                  <p v-if="edge.description">{{ edge.description }}</p>
                </div>
                <div class="inline-actions">
                  <button type="button" @click="openEditDependency(edge)">编辑</button>
                  <button type="button" class="danger" @click="removeDependency(edge)">删除</button>
                </div>
              </article>
            </div>
            <div>
              <h4>依赖当前服务</h4>
              <p v-if="directDependents.length === 0" class="section-empty">无调用方</p>
              <article v-for="edge in directDependents" :key="edge.id" class="dependency-row">
                <div>
                  <strong>{{ serviceLabel(edge.upstream_service_id) }}</strong>
                  <span>{{ dependencyTypeLabel[edge.dependency_type] }}</span>
                  <p v-if="edge.description">{{ edge.description }}</p>
                </div>
                <div class="inline-actions">
                  <button type="button" @click="openEditDependency(edge)">编辑</button>
                  <button type="button" class="danger" @click="removeDependency(edge)">删除</button>
                </div>
              </article>
            </div>
          </div>
        </section>
      </main>

      <main v-else class="service-placeholder">
        <div>
          <h2>从左侧菜单选择一个服务</h2>
          <p>点击服务器名称展开二级服务菜单，再点击服务查看详情。</p>
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
          <label>负责人<input v-model="serviceForm.owner" placeholder="团队或个人" /></label>
          <label>服务说明<textarea v-model="serviceForm.description" rows="3" /></label>
          <label>代码仓库<input v-model="serviceForm.repository_url" placeholder="https://..." /></label>
          <label>Grafana 仪表盘<input v-model="serviceForm.dashboard_url" placeholder="https://..." /></label>
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
            <label>Prometheus instance<input v-model="instanceForm.metric_instance" placeholder="host:9100" /></label>
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

    <div v-if="dependencyModal" class="modal-backdrop" @click.self="dependencyModal = false">
      <div class="modal" role="dialog" aria-modal="true" aria-label="服务依赖表单">
        <div class="modal-header">
          <h2>{{ editingDependency ? '编辑服务依赖' : '添加服务依赖' }}</h2>
          <button type="button" class="close-button" @click="dependencyModal = false">×</button>
        </div>
        <form class="modal-body" @submit.prevent="submitDependency">
          <label>
            关系方向
            <select v-model="dependencyForm.direction">
              <option value="depends_on">当前服务依赖目标服务</option>
              <option value="depended_by">目标服务依赖当前服务</option>
            </select>
          </label>
          <label>
            目标服务 *
            <select v-model="dependencyForm.target_service_id">
              <option v-for="service in targetServices" :key="service.id" :value="service.id">
                {{ service.name }}/{{ service.environment }}
              </option>
            </select>
          </label>
          <label>
            依赖类型
            <select v-model="dependencyForm.dependency_type">
              <option v-for="item in dependencyTypes" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>
          <label>关系说明<textarea v-model="dependencyForm.description" rows="3" /></label>
          <p v-if="formError" class="error">{{ formError }}</p>
          <div class="modal-actions">
            <button type="button" class="btn-ghost" @click="dependencyModal = false">取消</button>
            <button type="submit" class="btn-primary" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<style scoped>
.services-view {
  width: 100%;
  max-width: 1600px;
  margin: 0 auto;
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
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
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
  position: sticky;
  top: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  max-height: calc(100vh - 4rem);
  overflow-y: auto;
  padding: 0.75rem;
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
  overflow: hidden;
  border-radius: var(--radius-sm);
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
  background: var(--color-bg);
}

.server-menu-item.active {
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
  background: var(--color-bg);
}

.service-submenu-item.active {
  border-color: color-mix(in srgb, var(--color-accent) 35%, var(--color-border));
  background: #eef2ff;
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

.service-owner,
.service-counts {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.service-detail {
  grid-column: auto / -1;
  min-width: 0;
  padding: 1.25rem;
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

.service-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
  margin: 1.1rem 0 0;
  padding: 0.85rem 1rem;
  border-radius: var(--radius);
  background: var(--color-bg);
}

.service-meta dt {
  color: var(--color-text-muted);
  font-size: 0.7rem;
}

.service-meta dd {
  margin: 0.15rem 0 0;
  font-size: 0.82rem;
  overflow-wrap: anywhere;
}

.service-meta a {
  color: var(--color-accent);
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

.instance-card,
.dependency-row {
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

.dependency-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}

.dependency-columns > div > h4 {
  margin-bottom: 0.55rem;
  font-size: 0.82rem;
}

.dependency-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.dependency-row strong {
  display: block;
  font-size: 0.8rem;
  overflow-wrap: anywhere;
}

.dependency-row span {
  color: var(--color-text-muted);
  font-size: 0.68rem;
}

.dependency-row p {
  margin: 0.2rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.72rem;
  overflow-wrap: anywhere;
}

.service-placeholder {
  grid-column: auto / -1;
  display: grid;
  min-height: 480px;
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
  .directory-layout {
    grid-template-columns: 1fr;
  }

  .directory-menu-panel {
    position: static;
    max-height: none;
  }

  .service-detail,
  .service-placeholder {
    grid-column: auto;
  }

  .observability-grid {
    grid-template-columns: 1fr;
  }

  .metric-panel {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }
}

@media (max-width: 700px) {
  .services-view {
    padding: 1.25rem;
  }

  .instance-grid,
  .dependency-columns,
  .service-meta {
    grid-template-columns: 1fr;
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
</style>
