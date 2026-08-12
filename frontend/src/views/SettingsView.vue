<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchSkills } from '../api/skills'
import type { InstalledSkill, SkillDiagnostic } from '../types/skill'

const skills = ref<InstalledSkill[]>([])
const diagnostics = ref<SkillDiagnostic[]>([])
const loading = ref(true)
const refreshing = ref(false)
const error = ref<string | null>(null)

const enabledCount = computed(() => skills.value.filter((skill) => skill.enabled).length)
const diagnosticCount = computed(() => diagnostics.value.length)

function sourceLabel(source: string): string {
  return source === 'builtin' ? '项目内置' : source
}

async function loadSkills(refresh = false) {
  if (refresh) refreshing.value = true
  else loading.value = true
  error.value = null
  try {
    const result = await fetchSkills()
    skills.value = result.skills
    diagnostics.value = result.diagnostics
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

onMounted(() => loadSkills())
</script>

<template>
  <div class="settings-view">
    <header class="view-header">
      <div>
        <span class="view-kicker">SYSTEM / AGENT CAPABILITIES</span>
        <h1>设置</h1>
        <p>查看统一 Ops Agent 当前安装的能力说明，以及每轮对话可被自动激活的运维 Skill。</p>
      </div>
      <button
        type="button"
        class="refresh-button"
        :disabled="loading || refreshing"
        @click="loadSkills(true)"
      >
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M19 8a7.5 7.5 0 1 0 .35 7M19 4v4h-4" />
        </svg>
        {{ refreshing ? '扫描中…' : '重新扫描' }}
      </button>
    </header>

    <div class="settings-context" aria-label="设置分类">
      <span class="context-index">01</span>
      <span>
        <strong>Agent Skills</strong>
        <small>运行时能力目录</small>
      </span>
      <code>backend/skills/*/SKILL.md</code>
    </div>

    <section class="summary-grid" aria-label="Skill 安装概览">
      <article>
        <span>已发现</span>
        <strong>{{ skills.length }}</strong>
        <small>INSTALLED</small>
      </article>
      <article>
        <span>可被模型激活</span>
        <strong>{{ enabledCount }}</strong>
        <small>ENABLED</small>
      </article>
      <article :class="{ 'has-diagnostics': diagnosticCount > 0 }">
        <span>加载诊断</span>
        <strong>{{ diagnosticCount }}</strong>
        <small>{{ diagnosticCount ? 'REVIEW' : 'CLEAN' }}</small>
      </article>
    </section>

    <p v-if="error" class="feedback error" role="alert">
      <span>Skills 暂时无法读取</span>
      {{ error }}
    </p>

    <section v-if="diagnostics.length" class="diagnostics" aria-labelledby="diagnostics-title">
      <header>
        <span class="warning-mark" aria-hidden="true">!</span>
        <div>
          <h2 id="diagnostics-title">加载诊断</h2>
          <p>以下文件没有完全通过注册器校验；错误项不会进入 Agent 上下文。</p>
        </div>
      </header>
      <ul>
        <li v-for="diagnostic in diagnostics" :key="`${diagnostic.location}:${diagnostic.message}`">
          <span :class="['diagnostic-level', diagnostic.level]">
            {{ diagnostic.level === 'error' ? '错误' : '提醒' }}
          </span>
          <code>{{ diagnostic.location }}</code>
          <span>{{ diagnostic.message }}</span>
        </li>
      </ul>
    </section>

    <div v-if="loading" class="loading-state" role="status">
      <span class="scan-line" aria-hidden="true" />
      <strong>正在扫描 Skill 目录</strong>
      <small>校验元数据、触发词与工具关联…</small>
    </div>

    <section v-else-if="skills.length" class="skill-rack" aria-label="已安装 Skills">
      <article
        v-for="(skill, index) in skills"
        :key="skill.name"
        class="skill-card"
      >
        <div class="skill-number" aria-hidden="true">SK-{{ String(index + 1).padStart(2, '0') }}</div>

        <div class="skill-main">
          <header class="skill-header">
            <div>
              <div class="skill-meta-line">
                <span class="category">{{ skill.category }}</span>
                <span :class="['status', { disabled: !skill.enabled }]">
                  <i aria-hidden="true" />
                  {{ skill.enabled ? '已启用' : '未启用' }}
                </span>
              </div>
              <h2>{{ skill.name }}</h2>
              <p>{{ skill.description }}</p>
            </div>
            <dl class="identity-grid">
              <div>
                <dt>版本</dt>
                <dd>v{{ skill.version }}</dd>
              </div>
              <div>
                <dt>来源</dt>
                <dd>{{ sourceLabel(skill.source) }}</dd>
              </div>
            </dl>
          </header>

          <div class="skill-fields">
            <div>
              <span class="field-label">触发词</span>
              <div class="chip-list">
                <code v-for="trigger in skill.triggers" :key="trigger">{{ trigger }}</code>
                <small v-if="!skill.triggers.length">未配置自动触发词</small>
              </div>
            </div>
            <div>
              <span class="field-label">关联工具</span>
              <div class="chip-list tools">
                <code v-for="tool in skill.tools" :key="tool">{{ tool }}</code>
                <small v-if="!skill.tools.length">不依赖工具</small>
              </div>
            </div>
          </div>

          <div class="skill-location">
            <span>LOCATION</span>
            <code>{{ skill.location }}</code>
          </div>

          <details class="skill-instructions">
            <summary>
              <span>查看完整说明</span>
              <span class="summary-hint">运行时命中后注入本轮系统提示词</span>
              <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <path d="m6 8 4 4 4-4" />
              </svg>
            </summary>
            <pre>{{ skill.instructions }}</pre>
          </details>
        </div>
      </article>
    </section>

    <div v-else-if="!error" class="empty-state">
      <strong>还没有安装 Skill</strong>
      <p>在 <code>backend/skills/&lt;skill-name&gt;/SKILL.md</code> 添加有效文件后重新扫描。</p>
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  min-height: 100%;
  padding: 34px clamp(24px, 4vw, 58px) 56px;
  background:
    linear-gradient(rgba(19, 38, 48, 0.025) 1px, transparent 1px),
    var(--color-surface);
  background-size: 100% 32px;
}

.view-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--color-border);
}

.view-kicker {
  display: block;
  margin-bottom: 7px;
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.16em;
}

.view-header h1 {
  margin: 0;
  color: var(--color-text);
  font-family: var(--font-display);
  font-size: clamp(2rem, 4vw, 2.7rem);
  line-height: 1;
  letter-spacing: -0.035em;
}

.view-header p {
  max-width: 670px;
  margin: 12px 0 0;
  color: var(--color-text-muted);
}

.refresh-button {
  min-height: 40px;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  background: var(--color-surface-strong);
  font-weight: 600;
  box-shadow: var(--shadow-sm);
}

.refresh-button:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent-hover);
}

.refresh-button:disabled { opacity: 0.55; }
.refresh-button svg { width: 17px; height: 17px; }
.refresh-button path {
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.settings-context {
  min-height: 58px;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 22px;
  padding: 8px 16px;
  border-left: 3px solid var(--color-accent);
  background: linear-gradient(90deg, var(--color-interactive-bg), rgba(223, 240, 242, 0.15));
}

.context-index {
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.72rem;
  font-weight: 700;
}

.settings-context > span:nth-child(2) {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.settings-context strong { font-size: 0.9rem; }
.settings-context small { color: var(--color-text-muted); font-size: 0.72rem; }
.settings-context > code {
  margin-left: auto;
  color: #52636c;
  font-size: 0.72rem;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 22px 0 28px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--color-surface-strong);
  box-shadow: var(--shadow-sm);
}

.summary-grid article {
  min-height: 108px;
  display: grid;
  grid-template-columns: 1fr auto;
  align-content: center;
  gap: 2px 16px;
  padding: 18px 22px;
  border-right: 1px solid var(--color-border);
}

.summary-grid article:last-child { border-right: 0; }
.summary-grid span { color: var(--color-text-muted); font-size: 0.78rem; }
.summary-grid strong {
  grid-row: 1 / span 2;
  grid-column: 2;
  align-self: center;
  color: var(--color-text);
  font-family: var(--font-display);
  font-size: 2.15rem;
  line-height: 1;
}
.summary-grid small {
  color: #8a989f;
  font-family: var(--font-data);
  font-size: 0.62rem;
  letter-spacing: 0.11em;
}
.summary-grid .has-diagnostics strong { color: var(--color-status-unconfigured); }

.feedback {
  display: flex;
  flex-direction: column;
  margin: 0 0 20px;
  padding: 13px 16px;
  border: 1px solid #efc6c4;
  border-left: 4px solid var(--color-status-unreachable);
  border-radius: var(--radius-sm);
  color: #8b3432;
  background: #fff5f4;
}
.feedback span { color: #642624; font-weight: 700; }

.diagnostics {
  margin-bottom: 22px;
  border: 1px solid #edcfaa;
  border-radius: var(--radius);
  background: #fffaf3;
  overflow: hidden;
}
.diagnostics > header {
  display: flex;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid #efdbc1;
}
.warning-mark {
  width: 25px;
  height: 25px;
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: var(--color-status-unconfigured);
  font-weight: 800;
}
.diagnostics h2 { margin: 0; font-size: 0.94rem; }
.diagnostics p { margin: 3px 0 0; color: var(--color-text-muted); font-size: 0.78rem; }
.diagnostics ul { list-style: none; margin: 0; padding: 0; }
.diagnostics li {
  display: grid;
  grid-template-columns: auto minmax(140px, 0.6fr) 1fr;
  align-items: center;
  gap: 12px;
  padding: 11px 18px;
  border-bottom: 1px solid #f2e2cd;
  font-size: 0.78rem;
}
.diagnostics li:last-child { border-bottom: 0; }
.diagnostic-level { color: #99601e; font-weight: 700; }
.diagnostic-level.error { color: var(--color-status-unreachable); }
.diagnostics code { overflow-wrap: anywhere; color: #627078; }

.loading-state,
.empty-state {
  min-height: 250px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius);
  color: var(--color-text-muted);
  background: rgba(255, 255, 255, 0.55);
}
.loading-state strong,
.empty-state strong { color: var(--color-text); }
.loading-state small { margin-top: 4px; }
.scan-line {
  width: 78px;
  height: 3px;
  margin-bottom: 18px;
  overflow: hidden;
  background: #d5e1e4;
}
.scan-line::after {
  content: '';
  display: block;
  width: 32px;
  height: 100%;
  background: var(--color-accent);
  animation: scan 1.15s ease-in-out infinite alternate;
}
@keyframes scan { to { transform: translateX(46px); } }

.skill-rack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.skill-card {
  position: relative;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-strong);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.skill-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  background: var(--color-accent);
}

.skill-number {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 25px;
  border-right: 1px solid var(--color-border);
  color: #7e8d94;
  background: #f0f4f5;
  font-family: var(--font-data);
  font-size: 0.67rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.skill-main { min-width: 0; padding: 22px 24px 18px; }
.skill-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}
.skill-meta-line { display: flex; align-items: center; gap: 10px; }
.category {
  color: var(--color-accent-hover);
  font-family: var(--font-data);
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}
.status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-status-active);
  font-size: 0.7rem;
}
.status i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 3px rgba(35, 133, 109, 0.1);
}
.status.disabled { color: var(--color-status-untested); }
.skill-header h2 {
  margin: 5px 0 4px;
  font-family: var(--font-data);
  font-size: 1.08rem;
  letter-spacing: -0.02em;
}
.skill-header p {
  max-width: 720px;
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.84rem;
}

.identity-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(70px, auto));
  gap: 22px;
  flex: 0 0 auto;
  margin: 0;
}
.identity-grid dt,
.field-label,
.skill-location > span {
  color: #89969c;
  font-family: var(--font-data);
  font-size: 0.61rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.identity-grid dd { margin: 3px 0 0; font-size: 0.78rem; font-weight: 600; }

.skill-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-top: 19px;
  padding-top: 16px;
  border-top: 1px solid #e5ebed;
}
.chip-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.chip-list code {
  padding: 3px 7px;
  border: 1px solid #d8e2e5;
  border-radius: 4px;
  color: #42565f;
  background: #f2f6f7;
  font-size: 0.68rem;
}
.chip-list.tools code {
  border-color: #c5e0e3;
  color: #176d78;
  background: #eaf5f6;
}
.chip-list small { color: var(--color-text-muted); }

.skill-location {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  color: var(--color-text-muted);
}
.skill-location code { overflow-wrap: anywhere; font-size: 0.68rem; }

.skill-instructions {
  margin-top: 16px;
  border-top: 1px solid #e5ebed;
}
.skill-instructions summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--color-text);
  cursor: pointer;
  list-style: none;
  font-size: 0.8rem;
  font-weight: 600;
}
.skill-instructions summary::-webkit-details-marker { display: none; }
.summary-hint {
  color: var(--color-text-muted);
  font-size: 0.7rem;
  font-weight: 400;
}
.skill-instructions summary svg { width: 17px; height: 17px; margin-left: auto; }
.skill-instructions summary path {
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform 160ms ease;
  transform-origin: center;
}
.skill-instructions[open] summary path { transform: rotate(180deg); }
.skill-instructions pre {
  max-height: 430px;
  overflow: auto;
  margin: 0 0 4px;
  padding: 17px 18px;
  border: 1px solid #dce5e8;
  border-radius: var(--radius-sm);
  color: #31464f;
  background: #f5f8f9;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.73rem;
  line-height: 1.65;
}

.empty-state p { margin: 6px 0 0; }

@media (max-width: 900px) {
  .skill-header { flex-direction: column; }
  .identity-grid { width: 100%; }
}

@media (max-width: 680px) {
  .settings-view { padding: 24px 16px 38px; }
  .view-header { align-items: flex-start; flex-direction: column; }
  .refresh-button { width: 100%; justify-content: center; }
  .settings-context > code { display: none; }
  .summary-grid { grid-template-columns: 1fr; }
  .summary-grid article { min-height: 82px; border-right: 0; border-bottom: 1px solid var(--color-border); }
  .summary-grid article:last-child { border-bottom: 0; }
  .diagnostics li { grid-template-columns: auto 1fr; }
  .diagnostics li > span:last-child { grid-column: 1 / -1; }
  .skill-card { grid-template-columns: 1fr; }
  .skill-number {
    justify-content: flex-start;
    padding: 9px 15px 8px;
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }
  .skill-main { padding: 18px 16px 14px; }
  .skill-fields { grid-template-columns: 1fr; gap: 16px; }
  .summary-hint { display: none; }
}
</style>
