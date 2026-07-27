import { createApp, h } from 'vue'
import { NMessageProvider } from 'naive-ui'
import App from './App.vue'
import './style.css'

createApp({
  render: () => h(NMessageProvider, null, { default: () => h(App) }),
}).mount('#app')
