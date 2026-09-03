import { createApp } from 'vue'
import { createPinia } from 'pinia'

// v2 设计系统：Naive UI 推荐字体 + tailwind utilities + 全局基础
import 'vfonts/Lato.css'
import 'vfonts/FiraCode.css'
import './style.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
