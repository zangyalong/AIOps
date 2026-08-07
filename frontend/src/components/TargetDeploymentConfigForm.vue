<script setup lang="ts">
import type { DeploymentBundleSecrets, TargetDeploymentConfig } from '../types/server'

defineProps<{
  config: TargetDeploymentConfig
  secrets: DeploymentBundleSecrets
}>()
</script>

<template>
  <div class="deployment-group">
    <div class="deployment-heading">
      <div>
        <p class="group-title">环境部署</p>
        <p class="group-hint">
          首次下载并执行安装包；以后可保存配置后直接自动部署，无需重复下载。
        </p>
      </div>
      <label class="compact-field">
        环境
        <input v-model="config.environment" placeholder="prod" />
      </label>
    </div>

    <details open>
      <summary>
        <span>日志采集（Grafana Alloy）</span>
        <label class="switch-label" @click.stop>
          <input v-model="config.enable_alloy" type="checkbox" /> 启用
        </label>
      </summary>
      <div v-if="config.enable_alloy" class="details-body">
        <label>
          Loki 推送地址
          <input
            v-model="config.loki_push_url"
            placeholder="http://monitor.example.com:3100/loki/api/v1/push"
          />
        </label>
        <label class="checkbox-row">
          <input v-model="config.allow_docker_socket" type="checkbox" />
          允许 Alloy 读取 Docker socket（发现 Docker 服务且需要容器日志时开启）
        </label>
        <p class="component-hint">
          首次安装不会要求填写服务清单，也不会立即启动 Alloy。服务发现并注册后，再由审批 Tool
          生成映射并启动。
        </p>
      </div>
    </details>

    <details open>
      <summary><span>基础指标</span></summary>
      <div class="details-body">
        <section class="component-card">
          <label class="switch-label">
            <input v-model="config.enable_node_exporter" type="checkbox" />
            node_exporter（主机指标）
          </label>
          <label v-if="config.enable_node_exporter">
            镜像
            <input v-model="config.node_exporter_image" />
          </label>
        </section>

        <section class="component-card">
          <label class="switch-label">
            <input v-model="config.enable_cadvisor" type="checkbox" />
            cAdvisor（容器指标）
          </label>
          <template v-if="config.enable_cadvisor">
            <label>
              镜像
              <input v-model="config.cadvisor_image" />
            </label>
            <label>
              Docker 数据目录
              <input v-model="config.docker_root_dir" placeholder="/var/lib/docker" />
            </label>
          </template>
        </section>
      </div>
    </details>

    <details>
      <summary>
        <span>MySQL / MariaDB 指标</span>
        <label class="switch-label" @click.stop>
          <input v-model="config.enable_mysql_exporter" type="checkbox" /> 启用
        </label>
      </summary>
      <div v-if="config.enable_mysql_exporter" class="details-body form-grid">
        <label>镜像<input v-model="config.mysql_exporter_image" /></label>
        <label>监控用户名<input v-model="config.mysql_exporter_user" /></label>
        <label>数据库主机<input v-model="config.mysql_exporter_host" /></label>
        <label>
          数据库端口
          <input v-model.number="config.mysql_exporter_port" type="number" min="1" max="65535" />
        </label>
        <label>Docker 网络<input v-model="config.mysql_exporter_network" placeholder="host" /></label>
        <label>
          本次部署密码
          <input v-model="secrets.mysql_password" type="password" autocomplete="new-password" />
        </label>
      </div>
    </details>

    <details>
      <summary>
        <span>PostgreSQL 指标</span>
        <label class="switch-label" @click.stop>
          <input v-model="config.enable_postgres_exporter" type="checkbox" /> 启用
        </label>
      </summary>
      <div v-if="config.enable_postgres_exporter" class="details-body form-grid">
        <label>镜像<input v-model="config.postgres_exporter_image" /></label>
        <label>连接 URI（不含账号密码）<input v-model="config.postgres_exporter_uri" /></label>
        <label>监控用户名<input v-model="config.postgres_exporter_user" /></label>
        <label>Docker 网络<input v-model="config.postgres_exporter_network" placeholder="host" /></label>
        <label>
          本次部署密码
          <input v-model="secrets.postgres_password" type="password" autocomplete="new-password" />
        </label>
      </div>
    </details>

    <details>
      <summary>
        <span>Redis / Valkey 指标</span>
        <label class="switch-label" @click.stop>
          <input v-model="config.enable_redis_exporter" type="checkbox" /> 启用
        </label>
      </summary>
      <div v-if="config.enable_redis_exporter" class="details-body form-grid">
        <label>镜像<input v-model="config.redis_exporter_image" /></label>
        <label>连接地址<input v-model="config.redis_exporter_address" /></label>
        <label>监控用户名（可空）<input v-model="config.redis_exporter_user" /></label>
        <label>Docker 网络<input v-model="config.redis_exporter_network" placeholder="host" /></label>
        <label class="checkbox-row">
          <input v-model="config.redis_use_password" type="checkbox" />
          Redis 需要密码认证
        </label>
        <label v-if="config.redis_use_password">
          本次部署密码
          <input v-model="secrets.redis_password" type="password" autocomplete="new-password" />
        </label>
      </div>
    </details>

    <label class="checkbox-row exporter-start">
      <input v-model="config.start_exporters" type="checkbox" />
      安装时拉取镜像并启动已启用的 exporter
    </label>
    <p class="secret-note">
      数据库密码不会保存到服务器记录；下载或自动部署完成后前端立即清空。首次启用密码认证时必须填写，后续留空会复用目标服务器已有凭据。
    </p>
  </div>
</template>

<style scoped>
.deployment-group {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--color-bg) 65%, transparent);
}

.deployment-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.group-title {
  margin: 0;
  font-weight: 600;
}

.group-hint,
.component-hint,
.secret-note {
  margin: 0.2rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.78rem;
  line-height: 1.5;
}

.compact-field {
  width: 9rem;
  flex: 0 0 auto;
}

details {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
}

summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.7rem 0.8rem;
  cursor: pointer;
  font-size: 0.86rem;
  font-weight: 600;
}

.details-body {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  padding: 0 0.8rem 0.8rem;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.component-card {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding-top: 0.2rem;
}

.component-card + .component-card {
  padding-top: 0.8rem;
  border-top: 1px solid var(--color-border);
}

label {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  color: var(--color-text-muted);
  font-size: 0.78rem;
}

input:not([type='checkbox']) {
  width: 100%;
  box-sizing: border-box;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
}

.switch-label,
.checkbox-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 0.45rem;
  color: var(--color-text);
}

.switch-label input,
.checkbox-row input {
  margin: 0;
}

.exporter-start {
  font-size: 0.82rem;
}

.secret-note {
  padding: 0.6rem 0.7rem;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--color-accent) 8%, transparent);
}

@media (max-width: 680px) {
  .deployment-heading,
  .form-grid {
    display: flex;
    flex-direction: column;
  }

  .compact-field {
    width: 100%;
  }
}
</style>
