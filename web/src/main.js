import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import i18n from './locale'

const app = createApp(App)
// Element Plus 组件默认文案（时间选择器 OK/Cancel、弹窗按钮等）设为中文
app.use(ElementPlus, { locale: zhCn })
// 全局注册所有 Element Plus 图标，模板中可直接 <Odometer /> 使用
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}
app.use(router)
app.use(i18n)
app.mount('#app')
