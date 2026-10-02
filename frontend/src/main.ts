import { createApp, h } from 'vue'
import { NConfigProvider, NMessageProvider } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import App from './App.vue'
import './style.css'

// 品牌墨绿 #429488：UI 组件色与文字/LOGO 衬色统一同一色值；
// hover/pressed 按 naive 惯例做同色系亮/暗一档。
const brandTheme: GlobalThemeOverrides = {
  common: {
    primaryColor: '#429488',
    primaryColorHover: '#4fa396',
    primaryColorPressed: '#35786d',
    primaryColorSuppl: '#429488',
  },
}

createApp({
  render: () => h(NConfigProvider, { themeOverrides: brandTheme }, { default: () => h(NMessageProvider, null, { default: () => h(App) }) }),
}).mount('#app')
