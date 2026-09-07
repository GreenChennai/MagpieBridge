<template>
  <div class="dashboard">
    <!-- ===== 统计卡片 ===== -->
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="12" :sm="12" :md="6">
        <div class="stat-card">
          <div class="stat-icon" style="background: #e8f8ef; color: #07c160">
            <el-icon :size="24"><ChatDotRound /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value" :style="{ color: connected ? '#07c160' : '#f56c6c' }">
              {{ connected ? t('dashboard.connected') : t('dashboard.notFound') }}
            </div>
            <div class="stat-label">{{ t('dashboard.status') }}</div>
          </div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <div class="stat-card">
          <div class="stat-icon" style="background: #ecf5ff; color: #409eff">
            <el-icon :size="24"><Timer /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ formatUptime(status?.monitor?.uptime_seconds || 0) }}</div>
            <div class="stat-label">{{ t('dashboard.uptime') }}</div>
          </div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <div class="stat-card">
          <div class="stat-icon" style="background: #fdf6ec; color: #e6a23c">
            <el-icon :size="24"><Promotion /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">{{ status?.monitor?.messages_sent || 0 }}</div>
            <div class="stat-label">{{ t('dashboard.messagesSent') }}</div>
          </div>
        </div>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <div class="stat-card">
          <div class="stat-icon" style="background: #fef0f0; color: #f56c6c">
            <el-icon :size="24"><WarningFilled /></el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value" style="color: #f56c6c">{{ status?.monitor?.errors || 0 }}</div>
            <div class="stat-label">{{ t('dashboard.errors') }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- ===== 功能区 ===== -->
    <el-row :gutter="16" class="func-row">
      <el-col :xs="24" :md="14">
        <el-card shadow="never" class="page-card">
          <template #header>
            <div class="card-head">
              <el-icon color="#409eff"><EditPen /></el-icon>
              <span>{{ t('dashboard.quickSend') }}</span>
            </div>
          </template>
          <el-form :model="sendForm" label-position="top" class="card-form">
            <el-form-item :label="t('dashboard.contact')">
              <el-input v-model="sendForm.contact" :placeholder="t('dashboard.contactPlaceholder')" clearable />
            </el-form-item>
            <el-form-item :label="t('dashboard.message')">
              <el-input
                v-model="sendForm.message"
                type="textarea"
                :rows="3"
                :placeholder="t('dashboard.messagePlaceholder')"
                @keydown.enter.exact.prevent="handleSend"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="handleSend" :loading="sending">
                <el-icon style="margin-right: 4px"><Promotion /></el-icon>
                {{ t('dashboard.send') }}
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="10">
        <el-card shadow="never" class="page-card" style="margin-bottom: 16px">
          <template #header>
            <div class="card-head">
              <el-icon color="#e6a23c"><View /></el-icon>
              <span>{{ t('dashboard.browse') }}</span>
            </div>
          </template>
          <div class="browse-row">
            <el-switch v-model="browseEnabled" @change="handleBrowseChange" />
            <span class="muted-text">{{ t('dashboard.browseHint') }}</span>
          </div>
        </el-card>

        <el-card shadow="never" class="page-card">
          <template #header>
            <div class="card-head">
              <el-icon color="#f56c6c"><Monitor /></el-icon>
              <span>{{ t('dashboard.adminCommand') }}</span>
            </div>
          </template>
          <el-form :model="adminForm" label-position="top" class="card-form">
            <el-form-item :label="t('dashboard.command')">
              <el-input
                v-model="adminForm.command"
                :placeholder="t('dashboard.commandPlaceholder')"
                clearable
                @keydown.enter.exact.prevent="handleAdminCommand"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="warning" @click="handleAdminCommand" :loading="adminSending">
                <el-icon style="margin-right: 4px"><CaretRight /></el-icon>
                {{ t('dashboard.executeCommand') }}
              </el-button>
              <span class="muted-text" style="margin-left: 10px">{{ t('dashboard.commandHelp') }}</span>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getStatus, sendText, executeAdminCommand, getBrowse, setBrowse } from '../api'
import { ElMessage } from 'element-plus'

const { t } = useI18n()
const status = ref(null)
const sending = ref(false)
const adminSending = ref(false)
const browseEnabled = ref(false)
const sendForm = ref({ contact: '', message: '' })
const adminForm = ref({ command: '' })

const connected = computed(() => !!status.value?.engine?.window_found)

const formatUptime = (seconds) => {
  // 智能单位：非零单位从大到小，如 "1y 2d 3h 30m 24s"；全零显示 "0s"
  let s = Math.max(0, Math.floor(seconds || 0))
  const units = [['y', 31536000], ['d', 86400], ['h', 3600], ['m', 60], ['s', 1]]
  const parts = []
  for (const [u, size] of units) {
    if (s >= size) {
      parts.push(`${Math.floor(s / size)}${u}`)
      s %= size
    }
  }
  return parts.length ? parts.join(' ') : '0s'
}

const loadStatus = async () => {
  try {
    const res = await getStatus()
    status.value = res.data
  } catch (e) {
    console.error('Failed to load status:', e)
  }
}

const handleSend = async () => {
  if (!sendForm.value.contact || !sendForm.value.message) {
    ElMessage.warning(t('dashboard.fillWarning'))
    return
  }
  sending.value = true
  try {
    const res = await sendText(sendForm.value.contact, sendForm.value.message)
    if (res.data.success) {
      ElMessage.success(t('dashboard.msgSent'))
      sendForm.value.message = ''
    } else {
      ElMessage.error(t('dashboard.msgFailed'))
    }
  } catch (e) {
    ElMessage.error('Error: ' + (e.response?.data?.detail || e.message))
  } finally {
    sending.value = false
  }
}

const handleAdminCommand = async () => {
  if (!adminForm.value.command) {
    ElMessage.warning(t('dashboard.commandRequired'))
    return
  }
  adminSending.value = true
  try {
    const res = await executeAdminCommand(adminForm.value.command)
    if (res.data.success) {
      ElMessage.success(t('dashboard.commandExecuted'))
      adminForm.value.command = ''
    } else {
      ElMessage.error(res.data.error || t('dashboard.commandFailed'))
    }
  } catch (e) {
    ElMessage.error('Error: ' + (e.response?.data?.detail || e.message))
  } finally {
    adminSending.value = false
  }
}

const loadBrowse = async () => {
  try {
    const res = await getBrowse()
    browseEnabled.value = !!res.data.enabled
  } catch (e) {
    console.error('Failed to load browse state:', e)
  }
}

const handleBrowseChange = async (val) => {
  try {
    await setBrowse(val)
    ElMessage.success(val ? t('dashboard.browseOn') : t('dashboard.browseOff'))
  } catch (e) {
    console.error('Failed to set browse state:', e)
    ElMessage.error('Error: ' + (e.response?.data?.detail || e.message))
    browseEnabled.value = !val
  }
}

let timer = null
onMounted(() => {
  loadStatus()
  loadBrowse()
  timer = setInterval(loadStatus, 5000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.dashboard {
  max-width: 1280px;
  margin: 0 auto;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border-radius: 10px;
  padding: 18px 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  border: 1px solid #ebeef5;
  margin-bottom: 16px;
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-value {
  font-size: 17px;
  font-weight: 700;
  color: #303133;
  line-height: 1.3;
}

.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.func-row {
  margin-top: 4px;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
}

.browse-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.muted-text {
  color: #909399;
  font-size: 12px;
}
</style>
