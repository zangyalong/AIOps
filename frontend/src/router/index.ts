import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import IncidentsView from '../views/IncidentsView.vue'
import ServersView from '../views/ServersView.vue'

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
    { path: '/servers', name: 'servers', component: ServersView },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})
