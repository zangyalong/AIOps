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
  fetchServiceInstances,
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
const selectedInstances = computed(() =>
  instances.value.filter((instance) => instance.service_id === selectedId.value),
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
const filteredServices = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return services.value
  return services.value.filter((service) =>
    [service.name, service.environment, service.owner ?? ''].some((value) =>
      value.toLowerCase().includes(keyword),
    ),
  )
})

function instanceCount(serviceId: string): number {
  return instances.value.filter((instance) => instance.service_id === serviceId).length
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
watch(selectedId, () => void loadTopology(), { immediate: true })

function openService(serviceId: string) {
  void router.push(`/services/${serviceId}`)
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
  instanceForm.metric_instance = ''
  instanceForm.log_service = ''
  instanceForm.description = ''
  formError.value = null
}

function openCreateInstance() {
  resetInstanceForm()
  instanceModal.value = true
}

function openEditInstance(instance: ServiceInstance) {
  editingInstance.value = instance
  instanceForm.name = instance.name
  instanceForm.server_id = instance.server_id ?? ''
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
  const payload: ServiceInstanceInput = {
    service_id: selectedService.value.id,
    server_id: instanceForm.server_id || null,
    name: instanceForm.name.trim(),
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
        <p class="subtitle">统一管理逻辑服务、部署实例和有方向的依赖关系</p>
      </div>
      <button type="button" class="btn-primary" @click="openCreateService">+ 新增服务</button>
    </header>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading" class="hint">加载服务目录…</p>

    <div v-else class="directory-layout">
      <aside class="service-list-panel">
        <input v-model="search" class="service-search" placeholder="搜索服务、环境或负责人" />
        <p v-if="filteredServices.length === 0" class="list-empty">没有符合条件的服务</p>
        <button
          v-for="service in filteredServices"
          :key="service.id"
          type="button"
          class="service-list-item"
          :class="{ active: service.id === selectedId }"
          @click="openService(service.id)"
        >
          <span class="service-name">{{ service.name }}</span>
          <span class="environment-badge">{{ service.environment }}</span>
          <span class="service-owner">{{ service.owner || '未设置负责人' }}</span>
          <span class="service-counts">
            {{ instanceCount(service.id) }} 个实例 · {{ dependencyCount(service.id) }} 条关系
          </span>
        </button>
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
            <div><h3>部署实例</h3><p>将逻辑服务映射到服务器、Prometheus 和 Loki 标签</p></div>
            <button type="button" class="btn-secondary" @click="openCreateInstance">+ 添加实例</button>
          </div>
          <p v-if="selectedInstances.length === 0" class="section-empty">还没有部署实例。</p>
          <div v-else class="instance-grid">
            <article v-for="instance in selectedInstances" :key="instance.id" class="instance-card">
              <div class="card-heading">
                <h4>{{ instance.name }}</h4>
                <div class="inline-actions">
                  <button type="button" @click="openEditInstance(instance)">编辑</button>
                  <button type="button" class="danger" @click="removeInstance(instance)">删除</button>
                </div>
              </div>
              <p>{{ serverLabel(instance.server_id) }}</p>
              <dl>
                <div><dt>Prometheus</dt><dd>{{ instance.metric_instance || '未配置' }}</dd></div>
                <div><dt>Loki</dt><dd>{{ instance.log_service || '未配置' }}</dd></div>
              </dl>
              <p v-if="instance.description" class="instance-description">{{ instance.description }}</p>
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
          <h2>{{ services.length ? '选择一个服务查看详情' : '还没有服务' }}</h2>
          <p>服务目录把服务器、监控标签、变更记录和依赖关系连接到同一个逻辑实体。</p>
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
            <label>Prometheus instance<input v-model="instanceForm.metric_instance" placeholder="host:9100" /></label>
            <label>Loki service_name<input v-model="instanceForm.log_service" placeholder="orders-api" /></label>
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
  max-width: 1400px;
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
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
}

.service-list-panel,
.service-detail,
.service-placeholder {
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface);
  box-shadow: var(--shadow-sm);
}

.service-list-panel {
  position: sticky;
  top: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  max-height: calc(100vh - 4rem);
  overflow-y: auto;
  padding: 0.75rem;
}

.service-search {
  width: 100%;
  margin-bottom: 0.35rem;
}

.list-empty {
  padding: 1rem 0.5rem;
  font-size: 0.82rem;
  text-align: center;
}

.service-list-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.2rem 0.5rem;
  width: 100%;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 0.7rem;
  color: var(--color-text);
  background: transparent;
  text-align: left;
}

.service-list-item:hover {
  background: var(--color-bg);
}

.service-list-item.active {
  border-color: color-mix(in srgb, var(--color-accent) 35%, var(--color-border));
  background: #eef2ff;
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

.service-owner,
.service-counts {
  grid-column: 1 / -1;
  color: var(--color-text-muted);
  font-size: 0.72rem;
}

.service-detail {
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

.topology-section {
  border-top: 0;
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

  .service-list-panel {
    position: static;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    max-height: none;
  }

  .service-search,
  .list-empty {
    grid-column: 1 / -1;
  }
}

@media (max-width: 700px) {
  .services-view {
    padding: 1.25rem;
  }

  .service-list-panel,
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
}
</style>
