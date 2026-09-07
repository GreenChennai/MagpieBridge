<template>
  <div class="plugins-page">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <div style="display: flex; align-items: center; gap: 8px; font-size: 15px">
            <el-icon color="#722ed1"><Grid /></el-icon>
            <span>{{ t('plugins.title') }}</span>
          </div>
          <div>
            <el-button type="primary" plain @click="reload" :loading="reloading">
              <el-icon style="margin-right: 4px"><Refresh /></el-icon>
              {{ t('plugins.reload') }}
            </el-button>
            <el-button @click="loadPlugins">
              <el-icon style="margin-right: 4px"><Search /></el-icon>
              {{ t('plugins.refresh') }}
            </el-button>
          </div>
        </div>
      </template>

      <el-alert v-if="error" :title="error" type="warning" :closable="false" style="margin-bottom: 12px" />

      <el-table :data="plugins" style="width: 100%" v-loading="loading">
        <el-table-column :label="t('plugins.name')" prop="name" width="180">
          <template #default="{ row }">
            <span style="font-weight: 600">{{ row.name }}</span>
            <el-tag size="small" style="margin-left: 6px">v{{ row.version }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('plugins.description')" prop="description" min-width="220" />
        <el-table-column :label="t('plugins.author')" prop="author" width="120" />
        <el-table-column :label="t('plugins.events')" width="160">
          <template #default="{ row }">
            <el-tag v-for="ev in row.events" :key="ev" size="small" type="info" style="margin-right: 4px">
              {{ ev }}
            </el-tag>
            <span v-if="!row.events.length">-</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('plugins.actions')" width="160">
          <template #default="{ row }">
            <el-tag v-for="ac in row.actions" :key="ac" size="small" type="warning" style="margin-right: 4px">
              {{ ac }}
            </el-tag>
            <span v-if="!row.actions.length">-</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('plugins.status')" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.error" type="danger" size="small">{{ t('plugins.error') }}</el-tag>
            <el-tag v-else :type="row.enabled ? 'success' : 'info'" size="small">
              {{ row.enabled ? t('plugins.enabled') : t('plugins.disabled') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('plugins.operation')" width="220">
          <template #default="{ row }">
            <el-button
              v-if="row.settings && row.settings.length && !row.error"
              size="small"
              type="primary"
              plain
              @click="openSettings(row)"
            >
              {{ t('plugins.settings') }}
            </el-button>
            <el-button v-if="row.enabled && !row.error" size="small" type="danger" plain @click="toggle(row, false)">
              {{ t('plugins.disable') }}
            </el-button>
            <el-button v-else-if="!row.error" size="small" type="success" plain @click="toggle(row, true)">
              {{ t('plugins.enable') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="margin-top: 12px; color: var(--el-text-color-secondary); font-size: 12px">
        {{ t('plugins.hint') }}
      </div>
    </el-card>

    <!-- 插件设置弹窗 -->
    <el-dialog
      v-model="settingsVisible"
      :title="`${settingsName} - ${t('plugins.settingsTitle')}`"
      :width="isRich ? '720px' : '560px'"
      destroy-on-close
      top="4vh"
    >
      <!-- ===== 富编辑器：leads_forwarder（群下拉 + 时间段 + 成员 + 暂停受理） ===== -->
      <div v-if="isRich" class="rich-settings" v-loading="settingsLoading">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="发送格式">
              <el-select v-model="editor.send_mode" style="width: 100%">
                <el-option label="文字" value="text" />
                <el-option label="图片卡片" value="image" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="回复受理跟踪">
              <el-switch v-model="editor.reply_handling" active-text="开" inactive-text="关" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="超时自动转下一人">
              <el-switch v-model="editor.auto_forward" active-text="开" inactive-text="关" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="回复超时(分钟)">
              <el-input-number v-model="editor.reply_timeout" :min="1" :max="120" :disabled="!editor.auto_forward" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="时间显示">
          <el-radio-group v-model="hour12" size="small" style="display:flex">
            <el-radio-button :value="false">24 小时制</el-radio-button>
            <el-radio-button :value="true">12 小时制</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-divider content-position="left">成员库（可 @ 的人）</el-divider>
        <el-select
          v-model="editor.member_pool"
          multiple
          filterable
          allow-create
          default-first-option
          placeholder="输入后回车添加，或选择已有成员"
          style="width: 100%"
        >
          <el-option v-for="m in editor.member_pool" :key="m" :label="m" :value="m" />
        </el-select>

        <el-divider content-position="left">群 · 时间段分配</el-divider>
        <div style="display: flex; gap: 8px; margin-bottom: 12px">
          <el-select
            v-model="selectedGroupName"
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入并回车新建群"
            style="flex: 1"
            @change="onGroupChange"
          >
            <el-option v-for="g in editor.groups" :key="g.name" :label="g.name" :value="g.name" />
          </el-select>
          <el-button type="success" plain @click="addSlot" :disabled="!selectedGroupName">添加时间段</el-button>
          <el-tooltip content="删除当前群" placement="top">
            <el-button type="danger" plain :disabled="!selectedGroupName" @click="deleteGroup">删除群</el-button>
          </el-tooltip>
        </div>

        <template v-if="currentGroup">
          <div class="slot-list">
            <div v-for="(slot, si) in currentGroup.slots" :key="si" class="slot-card" :class="{ paused: slot.pause }">
              <div class="slot-head">
                <span class="slot-title">{{ slot.pause ? '⏸ 暂停受理' : '时段 ' + (si + 1) }}</span>
                <el-switch v-model="slot.pause" @change="onPauseChange" active-text="暂停受理" />
                <el-button text type="danger" @click="currentGroup.slots.splice(si, 1)">删除</el-button>
              </div>
              <div class="slot-body">
                <el-form inline size="small">
                  <el-form-item label="从">
                    <el-time-picker v-model="slot.start" value-format="HH:mm" :format="timeFormat" placeholder="开始" />
                  </el-form-item>
                  <el-form-item label="到">
                    <el-time-picker v-model="slot.end" value-format="HH:mm" :format="timeFormat" placeholder="结束" />
                  </el-form-item>
                </el-form>
                <div v-if="!slot.pause" class="slot-members">
                  <div class="slot-members-label">要 @ 的人（按先后轮流）</div>
                  <el-select
                    v-model="slot.members"
                    multiple
                    filterable
                    allow-create
                    default-first-option
                    placeholder="选择/添加成员"
                    style="width: 100%"
                  >
                    <el-option v-for="m in editor.member_pool" :key="m" :label="m" :value="m" />
                  </el-select>
                </div>
                <div v-else class="slot-paused-note">此时间段暂停受理：不会 @ 任何人，期间的线索将暂存，到其它时段再轮流发送。</div>
              </div>
            </div>
            <el-empty v-if="!currentGroup.slots.length" description="该群还没有时间段" :image-size="60" />
          </div>
        </template>
        <el-empty v-else description="请先在上方选择或新建一个群" :image-size="60" />
      </div>

      <!-- ===== 普通编辑器：按 schema 自动生成 ===== -->
      <el-form v-else label-width="140px" v-loading="settingsLoading">
        <el-form-item v-for="field in settingsSchema" :key="field.key" :label="field.label">
          <el-input
            v-if="field.type === 'textarea'"
            v-model="settingsValues[field.key]"
            type="textarea"
            :rows="4"
            :placeholder="field.description"
          />
          <el-input-number v-else-if="field.type === 'number'" v-model="settingsValues[field.key]" :min="0" />
          <el-switch v-else-if="field.type === 'boolean'" v-model="settingsValues[field.key]" />
          <el-select v-else-if="field.type === 'select' && field.options" v-model="settingsValues[field.key]" style="width: 100%">
            <el-option v-for="opt in field.options" :key="opt" :label="opt" :value="opt" />
          </el-select>
          <el-input v-else v-model="settingsValues[field.key]" :placeholder="field.description" />
          <div v-if="field.description" style="font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.4; margin-top: 4px">
            {{ field.description }}
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="settingsVisible = false">{{ t('plugins.cancel') }}</el-button>
        <el-button type="primary" :loading="settingsSaving" @click="saveSettings">
          {{ t('plugins.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPlugins, reloadPlugins, enablePlugin, disablePlugin, getPluginSettings, savePluginSettings } from '../api'

const { t } = useI18n()
const plugins = ref([])
const loading = ref(false)
const reloading = ref(false)
const error = ref('')

const settingsVisible = ref(false)
const settingsLoading = ref(false)
const settingsSaving = ref(false)
const settingsName = ref('')
const settingsSchema = ref([])
const settingsValues = ref({})

// rich editor state (leads_forwarder)
const isRich = ref(false)
const editor = ref({ send_mode: 'text', reply_handling: true, auto_forward: true, reply_timeout: 5, member_pool: [], groups: [] })
const selectedGroupName = ref('')
const hour12 = ref(false)
const timeFormat = computed(() => (hour12.value ? 'h:mm A' : 'HH:mm'))

const currentGroup = computed(() => {
  if (!selectedGroupName.value) return null
  return editor.value.groups.find(g => g.name === selectedGroupName.value) || null
})

const loadPlugins = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await getPlugins()
    plugins.value = res.data.plugins || []
  } catch (e) {
    error.value = e.response?.data?.detail || String(e)
  } finally {
    loading.value = false
  }
}

const reload = async () => {
  reloading.value = true
  try {
    await reloadPlugins()
    ElMessage.success(t('plugins.reloaded'))
    await loadPlugins()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || String(e))
  } finally {
    reloading.value = false
  }
}

const toggle = async (row, enable) => {
  try {
    if (enable) { await enablePlugin(row.name) } else { await disablePlugin(row.name) }
    ElMessage.success(enable ? t('plugins.enabled') : t('plugins.disabled'))
    await loadPlugins()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || String(e))
  }
}

const openSettings = async (row) => {
  settingsName.value = row.name
  settingsVisible.value = true
  settingsLoading.value = true
  settingsSchema.value = []
  settingsValues.value = {}
  isRich.value = false
  try {
    const res = await getPluginSettings(row.name)
    settingsSchema.value = res.data.schema || []
    settingsValues.value = { ...(res.data.values || {}) }
    // rich editor for leads_forwarder (has group_slots/member_pool schema keys)
    if (settingsSchema.value.some(f => f.key === 'group_slots')) {
      isRich.value = true
      hydrateRich(settingsValues.value)
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || String(e))
  } finally {
    settingsLoading.value = false
  }
}

function hydrateRich(vals) {
  const groups = parseGroupSlots(vals.group_slots)
  editor.value = {
    send_mode: vals.send_mode || 'text',
    reply_handling: vals.reply_handling !== false,
    auto_forward: vals.auto_forward !== false,
    reply_timeout: vals.reply_timeout || 5,
    member_pool: toList(vals.member_pool) || toList(vals.members) || [],
    groups,
  }
  selectedGroupName.value = groups.length ? groups[0].name : ''
}

function toList(v) {
  if (Array.isArray(v)) return v.filter(x => x)
  return String(v || '').split('\n').map(s => s.trim()).filter(Boolean)
}

function parseGroupSlots(text) {
  if (!text) return []
  try {
    const d = JSON.parse(text)
    if (Array.isArray(d)) return d
  } catch (e) { /* ignore */ }
  return []
}

function onGroupChange(v) {
  if (!v) return
  if (!editor.value.groups.some(g => g.name === v)) {
    editor.value.groups.push({ name: v, slots: [] })
  }
}

function addSlot() {
  if (!currentGroup.value) {
    // auto-create the group if a name is typed but not yet a real group
    if (selectedGroupName.value) {
      onGroupChange(selectedGroupName.value)
    } else {
      ElMessage.warning('请先在上方新建/选择群')
      return
    }
  }
  currentGroup.value.slots.push({ start: '09:00', end: '18:00', members: [], pause: false })
}

function deleteGroup() {
  const name = selectedGroupName.value
  if (!name) return
  const idx = editor.value.groups.findIndex(g => g.name === name)
  if (idx < 0) return
  ElMessageBox.confirm(`确定删除群「${name}」及其全部时间段配置吗？`, '删除确认', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(() => {
      editor.value.groups.splice(idx, 1)
      selectedGroupName.value = editor.value.groups.length ? editor.value.groups[0].name : ''
      ElMessage.success(`已删除群「${name}」`)
    })
    .catch(() => {})
}

const saveSettings = async () => {
  settingsSaving.value = true
  try {
    if (isRich.value) {
      const payload = {
        send_mode: editor.value.send_mode || 'text',
        reply_handling: editor.value.reply_handling !== false,
        auto_forward: editor.value.auto_forward !== false,
        reply_timeout: editor.value.reply_timeout || 5,
        member_pool: (editor.value.member_pool || []).join('\n'),
        group_slots: JSON.stringify(editor.value.groups),
      }
      await savePluginSettings(settingsName.value, payload)
      ElMessage.success(t('plugins.settingsSaved'))
      settingsVisible.value = false
    } else {
      await savePluginSettings(settingsName.value, settingsValues.value)
      ElMessage.success(t('plugins.settingsSaved'))
      settingsVisible.value = false
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || String(e))
  } finally {
    settingsSaving.value = false
  }
}

onMounted(loadPlugins)
</script>

<style scoped>
.plugins-page {
  max-width: 1280px;
  margin: 0 auto;
}

.rich-settings .el-form-item { margin-bottom: 10px; }
.rich-settings :deep(.el-form-item__content) { flex-wrap: wrap; }
.slot-list { display: flex; flex-direction: column; gap: 10px; }
.slot-card {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 14px;
  background: var(--el-bg-color);
}
.slot-card.paused { border-color: var(--el-color-warning); background: var(--el-color-warning-light-9); }
.slot-head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.slot-title { font-weight: 600; flex: 1; }
.slot-members-label { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; }
.slot-paused-note { font-size: 12px; color: var(--el-color-warning); }
</style>
