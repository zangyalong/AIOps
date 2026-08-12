<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchSkills } from '../api/skills'
import MarkdownContent from '../components/MarkdownContent.vue'
import type { InstalledSkill } from '../types/skill'

const skills = ref<InstalledSkill[]>([])
const query = ref('')
const selectedSkill = ref<InstalledSkill | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
let modalTrigger: HTMLElement | null = null

const filteredSkills = computed(() => {
  const normalizedQuery = query.value.trim().toLocaleLowerCase()
  if (!normalizedQuery) return skills.value
  return skills.value.filter((skill) =>
    `${skill.name} ${skill.description}`.toLocaleLowerCase().includes(normalizedQuery),
  )
})

async function loadSkills() {
  loading.value = true
  error.value = null
  try {
    const result = await fetchSkills()
    skills.value = result.skills
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    loading.value = false
  }
}

async function openSkill(skill: InstalledSkill, event: MouseEvent) {
  modalTrigger = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  selectedSkill.value = skill
  await nextTick()
  closeButton.value?.focus()
}

async function closeSkill() {
  selectedSkill.value = null
  await nextTick()
  modalTrigger?.focus()
  modalTrigger = null
}

function onWindowKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && selectedSkill.value) void closeSkill()
}

onMounted(() => {
  void loadSkills()
  window.addEventListener('keydown', onWindowKeydown)
})

onBeforeUnmount(() => window.removeEventListener('keydown', onWindowKeydown))
</script>

<template>
  <div class="skills-view">
    <div class="skills-main">
      <header class="skills-header">
        <p class="eyebrow">AGENT KNOWLEDGE</p>
        <h1>SKILLS</h1>
        <p class="subtitle">按需加载的运维调查方法与操作指南</p>
      </header>

      <label class="skill-search">
        <span class="sr-only">搜索 Skills</span>
        <input v-model="query" type="search" placeholder="搜索 Skills" autocomplete="off" />
      </label>

      <section class="installed-section" aria-labelledby="installed-title">
        <div class="section-heading">
          <h2 id="installed-title">已安装</h2>
          <span>{{ filteredSkills.length }} 项</span>
        </div>

        <p v-if="error" class="feedback error" role="alert">
          <strong>暂时无法读取 Skills</strong>
          <span>{{ error }}</span>
          <button type="button" @click="loadSkills">重试</button>
        </p>

        <div v-else-if="loading" class="feedback" role="status">
          <strong>正在读取 Skills</strong>
          <span>正在扫描已安装的说明文档…</span>
        </div>

        <div v-else-if="filteredSkills.length" class="skill-grid">
          <button
            v-for="skill in filteredSkills"
            :key="skill.name"
            type="button"
            class="skill-item"
            @click="openSkill(skill, $event)"
          >
            <span class="skill-copy">
              <strong>{{ skill.name }}</strong>
              <small>{{ skill.description }}</small>
            </span>
            <span class="view-action">查看</span>
          </button>
        </div>

        <div v-else class="feedback empty">
          <strong>{{ skills.length ? '没有匹配的 Skill' : '还没有安装 Skill' }}</strong>
          <span>{{ skills.length ? '换个关键词试试。' : '添加 SKILL.md 后会显示在这里。' }}</span>
        </div>
      </section>
    </div>

    <Teleport to="body">
      <div
        v-if="selectedSkill"
        class="modal-backdrop"
        role="presentation"
        @mousedown.self="closeSkill"
      >
        <section
          class="skill-dialog"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="`skill-title-${selectedSkill.name}`"
        >
          <header class="dialog-header">
            <div>
              <h2 :id="`skill-title-${selectedSkill.name}`">
                {{ selectedSkill.name }} <span>Skill</span>
              </h2>
              <p>{{ selectedSkill.description }}</p>
            </div>
            <button ref="closeButton" type="button" class="close-button" @click="closeSkill">
              关闭
            </button>
          </header>

          <div class="skill-document">
            <MarkdownContent :content="selectedSkill.instructions" />
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.skills-view {
  min-height: 100%;
  padding: 48px clamp(24px, 6vw, 76px) 72px;
  background: var(--color-surface);
}

.skills-main {
  width: min(840px, 100%);
  margin: 0 auto;
}

.skills-header {
  margin-bottom: 28px;
}

.eyebrow {
  margin: 0 0 7px;
  color: var(--color-accent);
  font-family: var(--font-data);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.14em;
}

h1 {
  margin: 0;
  font-family: var(--font-display);
  font-size: clamp(2rem, 4vw, 2.7rem);
  line-height: 1;
  letter-spacing: 0.04em;
}

.subtitle {
  margin: 10px 0 0;
  color: var(--color-text-muted);
  font-size: 1rem;
}

.skill-search {
  display: block;
  margin-bottom: 30px;
}

.skill-search input {
  width: 100%;
  min-height: 44px;
  padding-inline: 15px;
  border-color: var(--color-border);
  border-radius: 22px;
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.9) inset;
}

.section-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--color-border);
}

.section-heading h2 {
  margin: 0;
  font-size: 1.04rem;
}

.section-heading span {
  color: var(--color-text-muted);
  font-family: var(--font-data);
  font-size: 0.72rem;
}

.skill-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 20px;
  padding-top: 18px;
}

.skill-item {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 13px 14px;
  color: inherit;
  text-align: left;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  transition: background 150ms ease, border-color 150ms ease, transform 150ms ease;
}

.skill-item:hover {
  border-color: #e0e7ea;
  background: #f0f4f5;
  transform: translateY(-1px);
}

.skill-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.skill-copy strong {
  overflow: hidden;
  font-size: 0.92rem;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-copy small {
  display: -webkit-box;
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 0.77rem;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.view-action {
  flex: 0 0 auto;
  color: #839198;
  font-size: 0.73rem;
  opacity: 0;
  transition: opacity 150ms ease;
}

.skill-item:hover .view-action,
.skill-item:focus-visible .view-action {
  opacity: 1;
}

.feedback {
  min-height: 150px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: var(--color-text-muted);
  text-align: center;
}

.feedback strong {
  color: var(--color-text);
}

.feedback button {
  margin-top: 8px;
  padding: 7px 14px;
  color: var(--color-accent-contrast);
  border: 0;
  border-radius: 8px;
  background: var(--color-accent);
}

.feedback.error strong {
  color: var(--color-status-unreachable);
}

.modal-backdrop {
  position: fixed;
  z-index: 1000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(10, 24, 31, 0.44);
  backdrop-filter: blur(3px);
}

.skill-dialog {
  width: min(640px, 100%);
  max-height: min(760px, calc(100dvh - 48px));
  display: flex;
  flex-direction: column;
  padding: 22px;
  border: 1px solid rgba(255, 255, 255, 0.75);
  border-radius: 18px;
  background: var(--color-surface-strong);
  box-shadow: var(--shadow-lg);
}

.dialog-header {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  padding-bottom: 18px;
}

.dialog-header > div {
  min-width: 0;
  flex: 1;
}

.dialog-header h2 {
  margin: 0;
  font-size: 1.35rem;
  line-height: 1.3;
  overflow-wrap: anywhere;
}

.dialog-header h2 span {
  color: var(--color-text-muted);
  font-weight: 450;
}

.dialog-header p {
  margin: 8px 0 0;
  color: var(--color-text-muted);
  line-height: 1.55;
}

.close-button {
  flex: 0 0 auto;
  padding: 5px 8px;
  color: var(--color-text-muted);
  border: 0;
  border-radius: 7px;
  background: transparent;
  font-size: 0.78rem;
}

.close-button:hover {
  color: var(--color-text);
  background: var(--color-surface-inset);
}

.skill-document {
  min-height: 240px;
  overflow-y: auto;
  padding: 17px 18px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: #fbfcfc;
  color: #35464f;
  font-size: 0.86rem;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 720px) {
  .skills-view {
    padding: 32px 18px 48px;
  }

  .skill-grid {
    grid-template-columns: 1fr;
    gap: 5px;
  }

  .modal-backdrop {
    align-items: end;
    padding: 10px;
  }

  .skill-dialog {
    max-height: calc(100dvh - 20px);
    padding: 18px;
    border-radius: 16px;
  }

  .dialog-header {
    gap: 12px;
  }

  .skill-document {
    padding: 14px;
  }
}
</style>
