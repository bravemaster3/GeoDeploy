import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import router from './router'
import App from './App.vue'
import en from './i18n/en.json'
import fr from './i18n/fr.json'
import './style.css'

// Theme: dark is the default (index.html ships class="dark"); a saved preference wins.
// The toggle lives in the shell sidebar (Layout.vue) and writes the same key.
const savedTheme = localStorage.getItem('gd-theme')
if (savedTheme) document.documentElement.classList.toggle('dark', savedTheme === 'dark')

const i18n = createI18n({
  legacy: false,
  locale: navigator.language.startsWith('fr') ? 'fr' : 'en',
  fallbackLocale: 'en',
  messages: { en, fr },
})

createApp(App)
  .use(createPinia())
  .use(router)
  .use(i18n)
  .mount('#app')
