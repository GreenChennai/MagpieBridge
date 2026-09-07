<template>
  <div class="settings-page">
    <el-card shadow="never" class="page-card">
      <el-tabs v-model="activeTab" class="settings-tabs">
        <!-- ================= OneBot 配置 ================= -->
        <el-tab-pane :label="t('settings.onebotConfig')" name="onebot">
          <el-form label-width="130px" class="card-form" style="max-width: 620px">
            <el-form-item :key="'ob-http-host'" :label="t('settings.httpHost')">
              <el-input v-model="config.onebot.http_host" placeholder="127.0.0.1" />
            </el-form-item>
            <el-form-item :key="'ob-http-port'" :label="t('settings.httpPort')">
              <el-input-number v-model="config.onebot.http_port" :min="1" :max="65535" />
            </el-form-item>
            <el-form-item :key="'ob-ws-host'" :label="t('settings.wsHost')">
              <el-input v-model="config.onebot.ws_host" placeholder="127.0.0.1" />
            </el-form-item>
            <el-form-item :key="'ob-ws-port'" :label="t('settings.wsPort')">
              <el-input-number v-model="config.onebot.ws_port" :min="1" :max="65535" />
            </el-form-item>
            <el-form-item :key="'ob-token'" :label="t('settings.accessToken')">
              <el-input v-model="config.onebot.access_token" show-password placeholder="可选，留空表示无鉴权" />
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- ================= 微信配置 ================= -->
        <el-tab-pane :label="t('settings.wechatConfig')" name="wechat">
          <el-form label-width="130px" class="card-form" style="max-width: 620px">
            <el-form-item :key="'wx-x'" :label="t('settings.windowX')">
              <el-input-number v-model="config.wechat.window_position_x" />
            </el-form-item>
            <el-form-item :key="'wx-y'" :label="t('settings.windowY')">
              <el-input-number v-model="config.wechat.window_position_y" />
            </el-form-item>
            <el-form-item :key="'wx-w'" :label="t('settings.width')">
              <el-input-number v-model="config.wechat.window_width" :min="400" />
            </el-form-item>
            <el-form-item :key="'wx-h'" :label="t('settings.height')">
              <el-input-number v-model="config.wechat.window_height" :min="300" />
            </el-form-item>
            <el-form-item :key="'wx-ver'" :label="t('settings.wechatVersion')">
              <el-input v-model="config.wechat.adapter_version" disabled />
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- ================= 管理员配置 ================= -->
        <el-tab-pane :label="t('settings.adminConfig')" name="admin">
          <el-row :gutter="24">
            <el-col :xs="24" :md="14">
              <el-form label-width="140px" class="card-form">
                <el-form-item :key="'ad-enable'" :label="t('settings.enableAdmin')">
                  <el-switch v-model="config.admin.enabled" />
                  <el-tag
                    :type="config.admin.enabled ? 'success' : 'info'"
                    size="small"
                    style="margin-left: 10px"
                  >
                    {{ config.admin.enabled ? t('settings.enabled') : t('settings.disabled') }}
                  </el-tag>
                </el-form-item>
                <el-form-item :key="'ad-contacts'" :label="t('settings.adminContacts')">
                  <el-select
                    v-model="config.admin.admin_contacts"
                    multiple
                    filterable
                    allow-create
                    default-first-option
                    :placeholder="t('settings.addContact')"
                    style="width: 100%"
                  >
                    <el-option
                      v-for="contact in config.admin.admin_contacts"
                      :key="contact"
                      :label="contact"
                      :value="contact"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item :key="'ad-interval'" :label="t('settings.checkInterval')">
                  <el-input-number v-model="config.admin.check_interval_sec" :min="1" :max="60" />
                  <span style="margin-left: 8px">{{ t('settings.seconds') }}</span>
                </el-form-item>
                <el-form-item :key="'ad-prefix'" :label="t('settings.commandPrefix')">
                  <el-input v-model="config.admin.command_prefix" :placeholder="t('settings.prefixPlaceholder')" />
                </el-form-item>
              </el-form>
            </el-col>
            <el-col :xs="24" :md="10">
              <div class="hint-box">
                <div class="hint-box-title">{{ t('settings.commandFormat') }}</div>
                <div class="hint-box-body">
                  <p><strong>{{ t('settings.sendText') }}:</strong></p>
                  <code>你好世界-&gt;Rain</code>
                  <p class="hint-gap"><strong>{{ t('settings.sendImage') }}:</strong></p>
                  <code>/image C:\photo.png-&gt;Rain</code>
                  <p class="hint-gap"><strong>{{ t('settings.explicitText') }}:</strong></p>
                  <code>/text 你好-&gt;Rain</code>
                  <p class="hint-note" v-html="t('settings.adminNote')"></p>
                </div>
              </div>
            </el-col>
          </el-row>
        </el-tab-pane>

        <!-- ================= 邮件提醒 ================= -->
        <el-tab-pane :label="t('settings.alertConfig')" name="alert">
          <el-alert
            type="info"
            :closable="false"
            :title="t('settings.alertDesc')"
            style="margin-bottom: 18px"
          />
          <el-row :gutter="24">
            <el-col :xs="24" :md="14">
              <el-form label-width="140px" class="card-form">
                <el-form-item :key="'al-enable'" :label="t('settings.enableAlert')">
                  <el-switch v-model="config.alert.enabled" />
                  <el-tag
                    :type="config.alert.enabled ? 'success' : 'info'"
                    size="small"
                    style="margin-left: 10px"
                  >
                    {{ config.alert.enabled ? t('settings.enabled') : t('settings.disabled') }}
                  </el-tag>
                </el-form-item>
                <el-form-item :key="'al-host'" :label="t('settings.smtpHost')" required>
                  <el-input v-model="config.alert.smtp_host" placeholder="smtp.qq.com" />
                </el-form-item>
                <el-form-item :key="'al-port'" :label="t('settings.smtpPort')" required>
                  <el-input-number v-model="config.alert.smtp_port" :min="1" :max="65535" style="width: 150px" />
                </el-form-item>
                <el-form-item :key="'al-ssl'" :label="t('settings.useSSL')">
                  <el-checkbox v-model="config.alert.use_ssl" />
                  <span style="margin-left: 8px; color: #909399; font-size: 12px">
                    {{ config.alert.smtp_port === 465 ? '465 端口请勾选' : '587 端口请勿勾选' }}
                  </span>
                </el-form-item>
                <el-form-item :key="'al-user'" :label="t('settings.smtpUser')" required>
                  <el-input
                    v-model="config.alert.smtp_user"
                    :placeholder="t('settings.smtpUserPlaceholder')"
                    clearable
                  />
                  <div style="color: #909399; font-size: 12px; line-height: 1.6; margin-top: 2px">
                    {{ t('settings.smtpUserHint') }}
                  </div>
                </el-form-item>
                <el-form-item :key="'al-code'" :label="t('settings.authCode')" required>
                  <el-input
                    v-model="config.alert.auth_code"
                    type="password"
                    show-password
                    :placeholder="
                      config.alert.auth_code_set
                        ? t('settings.authCodeSet')
                        : t('settings.authCodePlaceholder')
                    "
                  />
                </el-form-item>
                <el-form-item :key="'al-recv'" :label="t('settings.receiver')" required>
                  <el-input
                    v-model="config.alert.receiver"
                    :placeholder="t('settings.receiverPlaceholder')"
                    clearable
                  />
                </el-form-item>
                <el-form-item :key="'al-cool'" :label="t('settings.cooldownSec')">
                  <el-input-number v-model="config.alert.cooldown_sec" :min="0" :max="86400" style="width: 150px" />
                  <span style="margin-left: 8px; color: #909399; font-size: 12px">{{ t('settings.cooldownHint') }}</span>
                </el-form-item>
                <el-form-item :key="'al-test'">
                  <el-button type="warning" :loading="testingAlert" @click="handleTestAlert">
                    <el-icon style="margin-right: 4px"><Message /></el-icon>
                    {{ t('settings.testAlert') }}
                  </el-button>
                  <span style="margin-left: 10px; color: #909399; font-size: 12px">
                    {{ t('settings.testAlertHint') }}
                  </span>
                </el-form-item>
              </el-form>
            </el-col>
            <el-col :xs="24" :md="10">
              <div class="hint-box">
                <div class="hint-box-title">{{ t('settings.qqMailHintTitle') }}</div>
                <div class="hint-box-body">
                  <p>1. {{ t('settings.qqMailHint1') }}</p>
                  <p>2. {{ t('settings.qqMailHint2') }}</p>
                  <p>3. {{ t('settings.qqMailHint3') }}</p>
                  <p>4. {{ t('settings.qqMailHint4') }}</p>
                  <p class="hint-note">5. {{ t('settings.qqMailHint5') }}</p>
                </div>
              </div>
            </el-col>
          </el-row>
        </el-tab-pane>
      </el-tabs>

      <!-- 底部操作条 -->
      <div class="settings-actions">
        <el-button type="primary" size="large" @click="handleSave" :loading="saving">
          <el-icon style="margin-right: 4px"><Check /></el-icon>
          {{ t('settings.save') }}
        </el-button>
        <el-button size="large" @click="loadConfig">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          {{ t('settings.reload') }}
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig, saveConfig, testAlert } from '../api'
import { ElMessage } from 'element-plus'

const { t } = useI18n()
const activeTab = ref('onebot')
const saving = ref(false)
const testingAlert = ref(false)

const config = ref({
  onebot: { http_host: '127.0.0.1', http_port: 3000, ws_host: '127.0.0.1', ws_port: 3001, access_token: '' },
  wechat: { window_position_x: 0, window_position_y: 0, window_width: 900, window_height: 600, adapter_version: '4.1.13' },
  web: {},
  human_sim: {},
  admin: { enabled: false, admin_contacts: [], check_interval_sec: 5, command_prefix: '' },
  alert: {
    enabled: false,
    smtp_host: 'smtp.qq.com',
    smtp_port: 465,
    use_ssl: true,
    smtp_user: '',
    auth_code: '',
    auth_code_set: false,
    receiver: '',
    cooldown_sec: 300,
  },
})

const loadConfig = async () => {
  try {
    const res = await getConfig()
    const loaded = res.data
    config.value = { ...config.value, ...loaded }
    // 后端字段缺失时不能整段覆盖掉前端默认值，逐段深合并
    for (const section of ['onebot', 'wechat', 'web', 'human_sim', 'admin', 'alert']) {
      if (loaded?.[section]) {
        config.value[section] = { ...config.value[section], ...loaded[section] }
      }
    }
    // 授权码后端只回传打码值，标记「已设置」，输入框留空表示不修改
    config.value.alert.auth_code = ''
    config.value.alert.auth_code_set = Boolean(loaded?.alert?.auth_code_set)
    ElMessage.success(t('settings.configLoaded'))
  } catch (e) {
    console.error('Failed to load config:', e)
    ElMessage.error(t('settings.configLoadFailed'))
  }
}

const buildPayload = () => ({
  onebot: config.value.onebot,
  wechat: config.value.wechat,
  admin: config.value.admin,
  alert: config.value.alert,
})

const handleSave = async () => {
  saving.value = true
  try {
    const res = await saveConfig(buildPayload())
    if (res.data.success) {
      ElMessage.success(t('settings.configSaved'))
    } else {
      ElMessage.error(t('settings.configSaveFailed'))
    }
  } catch (e) {
    console.error('Failed to save config:', e)
    ElMessage.error(t('settings.configSaveFailed') + ': ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

const handleTestAlert = async () => {
  testingAlert.value = true
  try {
    // 先把当前表单里的配置保存，再发测试邮件（避免测试用的还是旧配置）
    const saved = await saveConfig(buildPayload())
    if (!saved.data?.success) {
      ElMessage.error(t('settings.configSaveFailed'))
      return
    }
    const res = await testAlert()
    const msg =
      res.data.message || (res.data.success ? t('settings.testAlertOk') : t('settings.testAlertFail'))
    if (res.data.success) {
      ElMessage.success({ message: msg, duration: 6000, showClose: true })
    } else {
      // 错误原因较长（含排查建议），延长展示并允许手动关闭
      ElMessage({ type: 'error', message: msg, duration: 12000, showClose: true })
    }
  } catch (e) {
    console.error('Failed to send test alert:', e)
    ElMessage({
      type: 'error',
      message: t('settings.testAlertFail') + ': ' + (e.response?.data?.detail || e.message),
      duration: 12000,
      showClose: true,
    })
  } finally {
    testingAlert.value = false
  }
}

onMounted(loadConfig)
</script>

<style scoped>
.settings-page {
  max-width: 1280px;
  margin: 0 auto;
}

.settings-tabs :deep(.el-tabs__header) {
  margin-bottom: 20px;
}

.settings-tabs :deep(.el-tabs__item) {
  font-size: 14px;
}

.settings-actions {
  text-align: center;
  padding-top: 18px;
  border-top: 1px solid #f0f0f0;
  margin-top: 6px;
}

.hint-box {
  background: #fafbfc;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px 18px;
  font-size: 13px;
  color: #606266;
}

.hint-box-title {
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
  font-size: 14px;
}

.hint-box code {
  background: #f0f2f5;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
  color: #476582;
  word-break: break-all;
}

.hint-gap {
  margin-top: 10px;
}

.hint-note {
  margin-top: 12px;
  color: #909399;
  font-size: 12px;
  line-height: 1.7;
}
</style>
