import { createRouter, createWebHistory } from 'vue-router'
import AlertRulesView from '../views/AlertRulesView.vue'
import ChatView from '../views/ChatView.vue'
import IncidentsView from '../views/IncidentsView.vue'
import ServicesView from '../views/ServicesView.vue'
import ServersView from '../views/ServersView.vue'
import SkillsView from '../views/SkillsView.vue'

function lastChatPath(): string {
  const path = localStorage.getItem('aioms:last-chat-path') || '/chat'
  return path === '/chat' || path.startsWith('/chat/') ? path : '/chat'
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: lastChatPath },
    { path: '/chat', name: 'chat', component: ChatView },
    {
      path: '/chat/:conversationId',
      name: 'chat-conversation',
      component: ChatView,
    },
    { path: '/incidents', name: 'incidents', component: IncidentsView },
    { path: '/alert-rules', name: 'alert-rules', component: AlertRulesView },
    { path: '/services', name: 'services', component: ServicesView },
    { path: '/services/:serviceId', name: 'service-detail', component: ServicesView },
    { path: '/servers', name: 'servers', component: ServersView },
    { path: '/skills', name: 'skills', component: SkillsView },
    { path: '/settings', redirect: '/skills' },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})
