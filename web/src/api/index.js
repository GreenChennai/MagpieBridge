import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const getStatus = () => api.get('/status')
export const getMessages = (limit = 50, contact = null) =>
  api.get('/messages', { params: { limit, contact } })
export const getMessageStats = () => api.get('/messages/stats')
export const getMessageContacts = () => api.get('/messages/contacts')
export const deleteMessages = (payload) => api.post('/messages/delete', payload)
export const clearMessages = (payload) => api.post('/messages/clear', payload)
export const sendText = (contact, message) =>
  api.post('/send/text', { contact, message })
export const sendImage = (contact, filePath) =>
  api.post('/send/image', { contact, file_path: filePath })
export const getChatList = () => api.get('/chat/list')
export const initializeEngine = () => api.post('/engine/initialize')
export const getConfig = () => api.get('/config')
export const saveConfig = (config) => api.post('/config', config)
export const executeAdminCommand = (command) => api.post('/admin/command', { command })
export const getPlugins = () => api.get('/plugins')
export const reloadPlugins = () => api.post('/plugins/reload')
export const enablePlugin = (name) => api.post(`/plugins/${name}/enable`)
export const disablePlugin = (name) => api.post(`/plugins/${name}/disable`)
export const getPluginSettings = (name) => api.get(`/plugins/${name}/settings`)
export const savePluginSettings = (name, values) => api.post(`/plugins/${name}/settings`, values)
export const getBrowse = () => api.get('/browse')
export const setBrowse = (enabled) => api.post('/browse', { enabled })
export const testAlert = () => api.post('/alert/test')
