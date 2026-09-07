<template>
  <div class="messages-page">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="card-head">
          <el-icon color="#409eff"><ChatLineRound /></el-icon>
          <span>{{ t('messages.title') }}</span>
          <div class="head-actions">
            <el-select
              v-model="directionFilter"
              :placeholder="t('messages.direction')"
              clearable
              size="small"
              style="width: 110px"
              @change="loadMessages"
            >
              <el-option :label="t('messages.outgoing')" value="outgoing" />
              <el-option :label="t('messages.incoming')" value="incoming" />
            </el-select>
            <el-select
              v-model="statusFilter"
              :placeholder="t('messages.status')"
              clearable
              size="small"
              style="width: 110px"
              @change="loadMessages"
            >
              <el-option :label="t('messages.success')" value="success" />
              <el-option :label="t('messages.failed')" value="failed" />
              <el-option :label="t('messages.pending')" value="pending" />
            </el-select>
            <el-input
              v-model="contactFilter"
              :placeholder="t('messages.contact')"
              clearable
              size="small"
              style="width: 160px"
              @keyup.enter="loadMessages"
              @clear="loadMessages"
            />
            <el-button size="small" type="primary" plain @click="loadMessages">
              <el-icon style="margin-right: 4px"><Search /></el-icon>
              {{ t('messages.refresh') }}
            </el-button>
            <el-divider direction="vertical" />
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="!selectedIds.length"
              @click="confirmBatchDelete"
            >
              <el-icon style="margin-right: 4px"><Delete /></el-icon>
              {{ t('messages.batchDelete', { n: selectedIds.length }) }}
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="!activeContact"
              @click="confirmClearContact"
            >
              <el-icon style="margin-right: 4px"><DeleteFilled /></el-icon>
              {{ t('messages.clearContact') }}
            </el-button>
            <el-button
              size="small"
              type="danger"
              @click="confirmClearAll"
            >
              <el-icon style="margin-right: 4px"><Warning /></el-icon>
              {{ t('messages.clearAll') }}
            </el-button>
          </div>
        </div>
      </template>

      <div class="msg-body">
        <!-- 左侧：会话列表 -->
        <div class="contact-side">
          <div class="contact-item" :class="{ active: activeContact === '' }" @click="selectContact('')">
            <div class="contact-name">{{ t('messages.allContacts') }}</div>
            <el-tag size="small" type="info">{{ totalAll }}</el-tag>
          </div>
          <div
            v-for="c in contacts"
            :key="c.contact"
            class="contact-item"
            :class="{ active: activeContact === c.contact }"
            @click="selectContact(c.contact)"
          >
            <div class="contact-name" :title="c.contact">{{ c.contact }}</div>
            <div class="contact-meta">
              <el-tag size="small" type="primary">{{ c.total }}</el-tag>
            </div>
          </div>
          <div v-if="!contacts.length" class="contact-empty">
            {{ t('messages.noContacts') }}
          </div>
        </div>

        <!-- 右侧：消息表格 -->
        <div class="msg-table-wrap">
          <el-table
            ref="tableRef"
            :data="messages"
            style="width: 100%"
            max-height="560"
            v-loading="loading"
            stripe
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="42" />
            <el-table-column prop="created_at" :label="t('messages.time')" width="170" />
            <el-table-column prop="direction" :label="t('messages.direction')" width="90">
              <template #default="{ row }">
                <el-tag :type="row.direction === 'outgoing' ? 'primary' : 'success'" size="small">
                  {{ row.direction === 'outgoing' ? t('messages.outgoing') : t('messages.incoming') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="contact" :label="t('messages.contact')" width="150" show-overflow-tooltip />
            <el-table-column prop="content_type" :label="t('messages.type')" width="90" />
            <el-table-column prop="content" :label="t('messages.content')" show-overflow-tooltip />
            <el-table-column prop="status" :label="t('messages.status')" width="90">
              <template #default="{ row }">
                <el-tag
                  :type="row.status === 'success' ? 'success' : row.status === 'failed' ? 'danger' : 'info'"
                  size="small"
                >
                  {{ row.status === 'success' ? t('messages.success') : row.status === 'failed' ? t('messages.failed') : t('messages.pending') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('messages.operation')" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="danger" size="small" @click="confirmDeleteOne(row)">
                  {{ t('messages.delete') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="table-footer">
            <span class="muted-text">{{ t('messages.countHint', { n: messages.length }) }}</span>
            <el-pagination
              small
              layout="prev, pager, next"
              :total="totalAll"
              :page-size="pageSize"
              v-model:current-page="page"
              @current-change="loadMessages"
              style="margin-left: 12px"
            />
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { getMessages, getMessageContacts, deleteMessages, clearMessages } from '../api'

const { t } = useI18n()
const messages = ref([])
const contacts = ref([])
const loading = ref(false)
const directionFilter = ref('')
const statusFilter = ref('')
const contactFilter = ref('')
const activeContact = ref('')
const selectedIds = ref([])
const tableRef = ref(null)
const page = ref(1)
const pageSize = 200

const totalAll = computed(() => contacts.value.reduce((s, c) => s + (c.total || 0), 0))

const loadContacts = async () => {
  try {
    const res = await getMessageContacts()
    contacts.value = res.data.contacts || []
  } catch (e) {
    console.error('Failed to load contacts:', e)
  }
}

const loadMessages = async () => {
  loading.value = true
  try {
    // 分页按 contact（或全部）拉取，前端再过滤 direction/status
    const res = await getMessages(pageSize, activeContact.value || contactFilter.value || null)
    let list = res.data
    if (directionFilter.value) {
      list = list.filter((m) => m.direction === directionFilter.value)
    }
    if (statusFilter.value) {
      list = list.filter((m) => m.status === statusFilter.value)
    }
    messages.value = list
    selectedIds.value = []
    if (tableRef.value) {
      tableRef.value.clearSelection()
    }
  } catch (e) {
    console.error('Failed to load messages:', e)
  } finally {
    loading.value = false
  }
}

const selectContact = (contact) => {
  activeContact.value = contact
  page.value = 1
  loadContacts()
  loadMessages()
}

const onSelectionChange = (rows) => {
  selectedIds.value = rows.map((r) => r.id)
}

const confirmDeleteOne = (row) => {
  ElMessageBox.confirm(
    t('messages.deleteConfirmOne', { content: (row.content || '').slice(0, 30) }),
    t('messages.delete'),
    { type: 'warning', confirmButtonText: t('messages.delete'), cancelButtonText: t('messages.cancel') }
  )
    .then(async () => {
      const res = await deleteMessages({ ids: [row.id] })
      if (res.data.success) {
        ElMessage.success(t('messages.deleteOk'))
        loadContacts()
        loadMessages()
      }
    })
    .catch(() => {})
}

const confirmBatchDelete = () => {
  ElMessageBox.confirm(
    t('messages.batchDeleteConfirm', { n: selectedIds.value.length }),
    t('messages.batchDeleteTitle'),
    { type: 'warning', confirmButtonText: t('messages.delete'), cancelButtonText: t('messages.cancel') }
  )
    .then(async () => {
      const res = await deleteMessages({ ids: selectedIds.value })
      if (res.data.success) {
        ElMessage.success(t('messages.deleteOk'))
        loadContacts()
        loadMessages()
      }
    })
    .catch(() => {})
}

const confirmClearContact = () => {
  if (!activeContact.value) return
  ElMessageBox.confirm(
    t('messages.clearContactConfirm', { contact: activeContact.value }),
    t('messages.clearContact'),
    { type: 'warning', confirmButtonText: t('messages.delete'), cancelButtonText: t('messages.cancel') }
  )
    .then(async () => {
      const res = await clearMessages({ contact: activeContact.value })
      if (res.data.success) {
        ElMessage.success(t('messages.deleteOk'))
        loadContacts()
        loadMessages()
      }
    })
    .catch(() => {})
}

const confirmClearAll = () => {
  ElMessageBox.confirm(
    t('messages.clearAllConfirm'),
    t('messages.clearAll'),
    { type: 'error', confirmButtonText: t('messages.delete'), cancelButtonText: t('messages.cancel') }
  )
    .then(async () => {
      const res = await clearMessages({})
      if (res.data.success) {
        ElMessage.success(t('messages.deleteOk'))
        activeContact.value = ''
        loadContacts()
        loadMessages()
      }
    })
    .catch(() => {})
}

onMounted(() => {
  loadContacts()
  loadMessages()
})
</script>

<style scoped>
.messages-page {
  max-width: 1380px;
  margin: 0 auto;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  flex-wrap: wrap;
}

.head-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.msg-body {
  display: flex;
  gap: 12px;
}

/* ---- 左侧会话列表 ---- */
.contact-side {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid #ebeef5;
  padding-right: 10px;
  max-height: 640px;
  overflow-y: auto;
}

.contact-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2px;
}

.contact-item:hover {
  background: #f5f7fa;
}

.contact-item.active {
  background: #ecf5ff;
}

.contact-name {
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 130px;
}

.contact-empty {
  color: #909399;
  font-size: 12px;
  padding: 20px 10px;
  text-align: center;
}

/* ---- 右侧表格 ---- */
.msg-table-wrap {
  flex: 1;
  min-width: 0;
}

.table-footer {
  padding-top: 12px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.muted-text {
  color: #909399;
  font-size: 12px;
}
</style>
