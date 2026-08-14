<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()
const lastChatPath = ref(localStorage.getItem('aiops:last-chat-path') || '/chat')

watch(
  () => route.fullPath,
  (path) => {
    if (path === '/chat' || path.startsWith('/chat/')) {
      lastChatPath.value = path
      localStorage.setItem('aiops:last-chat-path', path)
    }
  },
  { immediate: true },
)

const items = computed(() => [
  {
    to: lastChatPath.value,
    label: '运维助手',
    shortLabel: '助手',
    path: 'M5 6.75A2.75 2.75 0 0 1 7.75 4h8.5A2.75 2.75 0 0 1 19 6.75v5.5A2.75 2.75 0 0 1 16.25 15H11l-4.25 3v-3.18A2.75 2.75 0 0 1 5 12.25v-5.5Z',
  },
  {
    to: '/incidents',
    label: '事件中心',
    shortLabel: '事件',
    path: 'M12 3.75 3.75 18h16.5L12 3.75Zm0 5v4.5m0 2.5v.1',
  },
  {
    to: '/alert-rules',
    label: '预警规则',
    shortLabel: '规则',
    path: 'M6.5 9.5a5.5 5.5 0 0 1 11 0c0 5 2 5 2 6.5h-15c0-1.5 2-1.5 2-6.5ZM10 19h4',
  },
  {
    to: '/services',
    label: '服务目录',
    shortLabel: '服务',
    path: 'M4.5 6.5 12 3l7.5 3.5L12 10 4.5 6.5Zm0 5L12 15l7.5-3.5M4.5 16.5 12 20l7.5-3.5',
  },
  {
    to: '/servers',
    label: '服务器',
    shortLabel: '主机',
    path: 'M5 4.5h14v6H5v-6Zm0 9h14v6H5v-6ZM8 7.5h.1M8 16.5h.1',
  },
  {
    to: '/memories',
    label: 'MEMORY',
    shortLabel: 'MEMORY',
    path: 'M8 5.5a4 4 0 0 1 8 0v.8a3.5 3.5 0 0 1 2.5 3.35v.7A3.5 3.5 0 0 1 16 13.7v.8a4 4 0 0 1-8 0v-.8a3.5 3.5 0 0 1-2.5-3.35v-.7A3.5 3.5 0 0 1 8 6.3v-.8ZM9 10h6M9.5 14h5',
  },
  {
    to: '/skills',
    label: 'SKILLS',
    shortLabel: 'SKILLS',
    path: 'M6 3.75h8l4 4v12.5H6V3.75Zm8 0v4h4M9 12h6M9 15.5h6',
  },
])
</script>

<template>
  <nav class="nav-sidebar" aria-label="主导航">
    <div class="signal-rail" aria-hidden="true">
      <span class="signal-dot" />
    </div>

    <div class="brand" aria-label="AIOps">
      <svg class="brand-mark" viewBox="0 0 40 40" fill="none" aria-hidden="true">
        <path d="M7 22h6l3-9 5 16 4-10 2 3h6" />
      </svg>
      <span class="brand-copy">
        <strong>AIOps</strong>
        <small>Operations desk</small>
      </span>
    </div>

    <div class="nav-section-label">工作台</div>
    <ul>
      <li v-for="item in items" :key="item.to">
        <RouterLink :to="item.to" class="nav-item">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path :d="item.path" />
          </svg>
          <span class="nav-label">{{ item.label }}</span>
          <span class="nav-label-short">{{ item.shortLabel }}</span>
        </RouterLink>
      </li>
    </ul>

    <div class="system-state">
      <span class="state-light" aria-hidden="true" />
      <span>
        <strong>控制台在线</strong>
        <small>安全审批已启用</small>
      </span>
    </div>
  </nav>
</template>

<style scoped>
.nav-sidebar {
  position: relative;
  isolation: isolate;
  width: 224px;
  flex-shrink: 0;
  height: 100%;
  overflow: hidden;
  padding: 0 18px 16px 24px;
  display: flex;
  flex-direction: column;
  color: #d9e4e8;
  background: #102733;
}

.signal-rail {
  position: absolute;
  inset: 0 auto 0 0;
  width: 6px;
  background: #0b202a;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
}

.signal-rail::before {
  content: '';
  position: absolute;
  inset: 0 1px;
  background: repeating-linear-gradient(to bottom, transparent 0 17px, rgba(255, 255, 255, 0.12) 18px 19px);
}

.signal-dot {
  position: absolute;
  top: 36px;
  left: 1px;
  width: 4px;
  height: 30px;
  border-radius: 4px;
  background: #46c2cc;
  box-shadow: 0 0 12px rgba(70, 194, 204, 0.9);
  animation: signal-breathe 3.2s ease-in-out infinite;
}

@keyframes signal-breathe {
  50% { opacity: 0.55; box-shadow: 0 0 5px rgba(70, 194, 204, 0.5); }
}

.brand {
  display: flex;
  flex: 0 0 84px;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid rgba(219, 233, 238, 0.12);
}

.brand-mark {
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  border: 1px solid rgba(82, 194, 204, 0.35);
  border-radius: 10px;
  background: rgba(32, 132, 145, 0.12);
}

.brand-mark path {
  stroke: #62c6cf;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.brand-copy {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.brand-copy strong {
  color: #f2f7f8;
  font-family: var(--font-display);
  font-size: 1.2rem;
  font-weight: 700;
  letter-spacing: 0.09em;
}

.brand-copy small,
.system-state small {
  margin-top: 5px;
  color: #78919d;
  font-family: var(--font-data);
  font-size: 0.62rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.nav-section-label {
  margin: 22px 10px 8px;
  color: #6f8995;
  font-family: var(--font-data);
  font-size: 0.63rem;
  letter-spacing: 0.14em;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 44px;
  padding: 0 11px;
  border: 1px solid transparent;
  border-radius: 9px;
  color: #9fb2bb;
  font-size: 0.89rem;
  font-weight: 500;
  text-decoration: none;
  transition: color 160ms ease, background 160ms ease, border-color 160ms ease;
}

.nav-item svg {
  width: 19px;
  height: 19px;
  flex: 0 0 auto;
}

.nav-item path {
  stroke: currentColor;
  stroke-width: 1.65;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.nav-item:hover {
  color: #eef6f7;
  background: rgba(255, 255, 255, 0.055);
}

.nav-item.router-link-active {
  color: #eefdff;
  border-color: rgba(83, 192, 201, 0.22);
  background: rgba(30, 137, 151, 0.2);
}

.nav-item.router-link-active::after {
  content: '';
  position: absolute;
  right: 8px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #55c6cf;
  box-shadow: 0 0 8px rgba(85, 198, 207, 0.75);
}

.nav-label-short {
  display: none;
}

.system-state {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-top: auto;
  padding: 13px 10px;
  border-top: 1px solid rgba(219, 233, 238, 0.12);
}

.state-light {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #55bf9d;
  box-shadow: 0 0 0 4px rgba(85, 191, 157, 0.1);
}

.system-state > span:last-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.system-state strong {
  color: #b9c9cf;
  font-size: 0.76rem;
  font-weight: 500;
}

@media (max-width: 760px) {
  .nav-sidebar {
    order: 2;
    width: 100%;
    height: 68px;
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom));
    overflow: visible;
  }

  .signal-rail,
  .brand,
  .nav-section-label,
  .system-state {
    display: none;
  }

  ul {
    height: 100%;
    flex-direction: row;
    justify-content: space-around;
    gap: 2px;
  }

  li {
    flex: 1;
  }

  .nav-item {
    height: 100%;
    min-height: 0;
    flex-direction: column;
    justify-content: center;
    gap: 2px;
    padding: 3px;
    font-size: 0.66rem;
  }

  .nav-item svg {
    width: 18px;
    height: 18px;
  }

  .nav-item.router-link-active::after {
    top: 3px;
    right: 50%;
    width: 16px;
    height: 2px;
    border-radius: 2px;
    transform: translateX(50%);
  }

  .nav-label {
    display: none;
  }

  .nav-label-short {
    display: inline;
  }
}
</style>
