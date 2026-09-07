<template>
  <el-container class="app-shell">
    <!-- ===== 左侧深色侧栏 ===== -->
    <el-aside width="220px" class="app-aside">
      <div class="app-logo">
        <div class="logo-badge">
          <el-icon :size="20"><ChatDotRound /></el-icon>
        </div>
        <div class="logo-text">
          <div class="logo-title">MagpieBridge</div>
          <div class="logo-sub">{{ t('nav.subtitle') }}</div>
        </div>
      </div>

      <el-menu
        :default-active="route.path"
        router
        class="app-menu"
        background-color="#001529"
        text-color="#9aa4b2"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>{{ t('nav.dashboard') }}</span>
        </el-menu-item>
        <el-menu-item index="/messages">
          <el-icon><ChatDotRound /></el-icon>
          <span>{{ t('nav.messages') }}</span>
        </el-menu-item>
        <el-menu-item index="/plugins">
          <el-icon><Grid /></el-icon>
          <span>{{ t('nav.plugins') }}</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>{{ t('nav.settings') }}</span>
        </el-menu-item>
      </el-menu>

      <div class="app-aside-footer">
        <el-select v-model="locale" size="small" style="width: 120px">
          <el-option label="中文" value="zh-CN" />
          <el-option label="English" value="en" />
        </el-select>
      </div>
    </el-aside>

    <!-- ===== 右侧主体 ===== -->
    <el-container class="app-body">
      <el-header class="app-header" height="56px">
        <div class="header-title">
          <span class="header-crumb">MagpieBridge</span>
          <span class="header-sep">/</span>
          <span class="header-page">{{ currentTitle }}</span>
        </div>
        <div class="header-right">
          <span class="header-hint">{{ t('nav.serviceHint') }}</span>
          <el-tag size="small" type="success" effect="light" round>{{ version }}</el-tag>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { watch } from 'vue'

const route = useRoute()
const { t, locale } = useI18n()

const version = 'v0.4.14'

const currentTitle = computed(() => {
  const map = {
    '/': t('nav.dashboard'),
    '/messages': t('nav.messages'),
    '/plugins': t('nav.plugins'),
    '/settings': t('nav.settings'),
  }
  return map[route.path] || t('nav.dashboard')
})

watch(locale, (val) => {
  localStorage.setItem('locale', val)
})

const savedLocale = localStorage.getItem('locale')
if (savedLocale) {
  locale.value = savedLocale
}
</script>

<style>
/* ---- 全局布局（经典后台：深色侧栏 + 白顶栏 + 浅灰内容区） ---- */
html,
body,
#app {
  height: 100%;
  margin: 0;
  padding: 0;
}

.app-shell {
  height: 100vh;
}

/* 侧栏 */
.app-aside {
  display: flex;
  flex-direction: column;
  background: #001529;
  overflow: hidden;
}

.app-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.logo-badge {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: linear-gradient(135deg, #07c160, #0a8f4c);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.logo-title {
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.3px;
}

.logo-sub {
  color: #5d6a7d;
  font-size: 11px;
  margin-top: 2px;
}

.app-menu {
  flex: 1;
  border-right: none;
  padding-top: 6px;
}

.app-menu .el-menu-item {
  height: 46px;
  line-height: 46px;
  margin: 2px 8px;
  border-radius: 6px;
}

.app-menu .el-menu-item:hover {
  background: rgba(255, 255, 255, 0.06) !important;
}

.app-menu .el-menu-item.is-active {
  background: linear-gradient(90deg, rgba(7, 193, 96, 0.22), rgba(7, 193, 96, 0.06)) !important;
  color: #fff !important;
}

.app-menu .el-menu-item.is-active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 2px;
  background: #07c160;
}

.app-aside-footer {
  padding: 14px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.app-aside-footer .el-select {
  width: 100%;
}

/* 顶栏 */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 20px;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
}

.header-crumb {
  color: #909399;
}

.header-sep {
  color: #c0c4cc;
}

.header-page {
  color: #303133;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-hint {
  color: #909399;
  font-size: 12px;
}

/* 内容区 */
.app-main {
  background: #f0f2f5;
  padding: 16px 20px;
  overflow-y: auto;
}

/* 页面通用卡片标题 */
.page-card .el-card__header {
  font-weight: 600;
  color: #303133;
}

/* 表单卡片内边距微调 */
.card-form .el-form-item {
  margin-bottom: 16px;
}
</style>
