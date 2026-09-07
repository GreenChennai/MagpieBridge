import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import MessageLog from './views/MessageLog.vue'
import Settings from './views/Settings.vue'
import Plugins from './views/Plugins.vue'

const routes = [
  { path: '/', name: 'Dashboard', component: Dashboard },
  { path: '/messages', name: 'Messages', component: MessageLog },
  { path: '/plugins', name: 'Plugins', component: Plugins },
  { path: '/settings', name: 'Settings', component: Settings },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
