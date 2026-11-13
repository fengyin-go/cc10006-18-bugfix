<template>
  <section class="page" data-module="quality">
    <header class="page-head">
      <div>
        <h2>质量监控管理</h2>
        <p class="page-desc">维护质量监控指标的分页边界、检测结论、告警恢复与质量回执，空结果与检测未运行明确区分。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记质量指标</button>
        <button class="btn" type="button" @click="exportRows">导出质量监控清单</button>
        <button class="btn" type="button" @click="exportAllReceipts">导出质量回执</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.tone">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reloadList(1)">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>监控对象</span>
        <select v-model="targetFilter" @change="onTargetChange">
          <option value="">全部对象</option>
          <option v-for="target in targets" :key="target" :value="target">{{ target }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>页码</span>
        <input v-model.number="pageInput" type="number" min="1" style="width: 84px" />
      </label>
      <label class="filter-item">
        <span>每页条数</span>
        <input v-model.number="sizeInput" type="number" min="1" max="200" style="width: 96px" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>检测状态</th>
          <th>告警计数</th>
          <th>失败/中断原因</th>
          <th>最近回执</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td><span class="state-badge" :class="badgeClass(row['检测状态'])">{{ row['检测状态'] }}</span></td>
          <td>{{ row['告警计数'] ?? 0 }}</td>
          <td>{{ row['失败原因'] || '—' }}</td>
          <td>{{ row['最近回执'] || '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openDetection(row)">发起检测</button>
            <button class="link" type="button" @click="recoverRow(row)">恢复告警</button>
            <button class="link" type="button" @click="exportRowReceipts(row)">回执</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 5" class="empty-state">
            <strong>{{ emptyTitle }}</strong>
            <span v-if="emptyHint">：{{ emptyHint }}</span>
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条质量监控记录，第 {{ page }} 页 / 每页 {{ size }} 条</span>
      <span class="pager">
        <button class="btn ghost" type="button" :disabled="page <= 1" @click="reloadList(page - 1)">上一页</button>
        <button class="btn ghost" type="button" :disabled="page * size >= total" @click="reloadList(page + 1)">下一页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="infoMessage" class="info-text">{{ infoMessage }}</span>
    </footer>

    <div class="panel-grid">
      <section class="side-panel">
        <header class="panel-head">
          <h3>质量告警清单{{ targetFilter ? `（对象：${targetFilter}）` : '' }}</h3>
          <label class="panel-check">
            <input v-model="activeOnly" type="checkbox" @change="reloadAlerts" />
            仅看未恢复
          </label>
        </header>
        <table class="data-table compact">
          <thead>
            <tr><th>告警编号</th><th>监控对象</th><th>级别</th><th>原因</th><th>状态</th><th>产生时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="alert in alerts" :key="alert.alert_id">
              <td>{{ alert.alert_id }}</td>
              <td>{{ alert['监控对象'] }}</td>
              <td>{{ alert['告警级别'] }}</td>
              <td>{{ alert['告警原因'] }}</td>
              <td><span class="state-badge" :class="alert['状态'] === '未恢复' ? 'tone-fail' : 'tone-ok'">{{ alert['状态'] }}</span></td>
              <td>{{ alert['产生时间'] }}</td>
              <td>
                <button
                  v-if="alert['状态'] === '未恢复'"
                  class="link"
                  type="button"
                  @click="recoverAlert(String(alert.alert_id))"
                >
                  恢复
                </button>
                <span v-else>—</span>
              </td>
            </tr>
            <tr v-if="!alerts.length">
              <td colspan="7" class="empty-state">{{ alertsEmptyHint }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="side-panel">
        <header class="panel-head">
          <h3>恢复记录{{ targetFilter ? `（对象：${targetFilter}）` : '' }}</h3>
        </header>
        <table class="data-table compact">
          <thead>
            <tr><th>恢复编号</th><th>关联告警</th><th>监控对象</th><th>恢复原因</th><th>来源</th><th>恢复时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="record in recoveries" :key="record.recovery_id">
              <td>{{ record.recovery_id }}</td>
              <td>{{ record.alert_id }}</td>
              <td>{{ record['监控对象'] }}</td>
              <td>{{ record['恢复原因'] }}</td>
              <td>{{ record['恢复来源'] }}</td>
              <td>{{ record['恢复时间'] }}</td>
            </tr>
            <tr v-if="!recoveries.length">
              <td colspan="6" class="empty-state">{{ targetFilter ? '该监控对象暂无恢复记录' : '暂无恢复记录：尚未发生过告警恢复' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <div v-if="detectionOpen" class="modal-mask" @click.self="detectionOpen = false">
      <form class="modal-card" @submit.prevent="submitDetection">
        <h3>发起质量检测 · {{ detectionRow?.['监控对象'] }}</h3>
        <label class="filter-item">
          <span>检测结论</span>
          <select v-model="detectionResult">
            <option value="成功">成功（通过，重试成功会自动恢复历史告警）</option>
            <option value="失败">失败（必须填写失败原因）</option>
            <option value="中断">中断（必须填写中断原因）</option>
          </select>
        </label>
        <label class="filter-item">
          <span>原因说明</span>
          <textarea v-model="detectionReason" rows="3" :placeholder="detectionResult === '成功' ? '可选：通过说明' : '必填：说明失败或中断原因'"></textarea>
        </label>
        <footer class="modal-actions">
          <button class="btn ghost" type="button" @click="detectionOpen = false">取消</button>
          <button class="btn primary" type="submit">提交检测</button>
        </footer>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type AlertRow = Record<string, string | number>
type RecoveryRow = Record<string, string>
type Summary = Record<string, number>

interface PagePayload {
  items: Row[]
  total: number
  page: number
  size: number
  empty_code: string
  message: string
}

const ENDPOINT = '/api/quality'
const columns = ['指标编号', '监控对象', '指标类型', '阈值范围', '检测周期', '告警级别', '通知渠道', '恢复策略']
const actions = ['提交校验', '启用监控', '停用监控']
const statuses = ['草稿', '待校验', '已启用', '已停用']

const rows = ref<Row[]>([])
const alerts = ref<AlertRow[]>([])
const recoveries = ref<RecoveryRow[]>([])
const targets = ref<string[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const pageInput = ref(1)
const sizeInput = ref(20)
const emptyCode = ref('')
const errorMessage = ref('')
const infoMessage = ref('')
const filters = ref<Record<string, string>>({})
const targetFilter = ref('')
const statusFilter = ref('')
const activeOnly = ref(false)
const filterFields = columns.slice(0, 2)
const summary = ref<Summary>({})

const detectionOpen = ref(false)
const detectionRow = ref<Row | null>(null)
const detectionResult = ref('成功')
const detectionReason = ref('')

const stats = computed(() => [
  { label: '质量指标总数', value: summary.value['指标总数'] ?? 0, tone: '' },
  { label: '未检测', value: summary.value['未检测'] ?? 0, tone: 'tone-muted' },
  { label: '健康', value: summary.value['健康'] ?? 0, tone: 'tone-ok' },
  { label: '检测失败', value: summary.value['失败'] ?? 0, tone: 'tone-fail' },
  { label: '检测中断', value: summary.value['中断'] ?? 0, tone: 'tone-warn' },
  { label: '未恢复告警', value: summary.value['未恢复告警'] ?? 0, tone: 'tone-fail' },
])

const emptyTitle = computed(() => {
  if (errorMessage.value) return '列表读取失败'
  if (emptyCode.value === 'PAGE_OUT_OF_RANGE') return '页码超出范围'
  if (emptyCode.value === 'NO_RECORD') return '未检测：暂无符合条件的质量指标记录'
  return '未检测：质量监控尚未运行或暂无数据'
})

const emptyHint = computed(() => {
  if (emptyCode.value === 'PAGE_OUT_OF_RANGE') return '请求页没有数据，这不是健康检测结论，请返回有效页码'
  if (emptyCode.value === 'NO_RECORD') return '合法空结果（无记录），与「检测未运行」不是同一种状态'
  return ''
})

const alertsEmptyHint = computed(() =>
  targetFilter.value
    ? '该监控对象暂无告警；未检测不等于健康，告警与其他对象隔离'
    : '暂无告警记录；空告警清单不代表检测健康，请以检测状态为准',
)

function badgeClass(state: string | number | null | undefined): string {
  switch (state) {
    case '健康':
      return 'tone-ok'
    case '失败':
      return 'tone-fail'
    case '中断':
      return 'tone-warn'
    case '检测中':
      return 'tone-running'
    default:
      return 'tone-muted'
  }
}

function buildListQuery(targetPage: number, targetSize: number): string {
  const params = new URLSearchParams()
  // 中文筛选字段映射到后端实际入参：指标编号→keyword，监控对象→target
  Object.entries(filters.value).forEach(([key, value]) => {
    if (!value) return
    if (key === '指标编号') params.set('keyword', value)
    else if (key === '监控对象') params.set('target', value)
    else params.set(key, value)
  })
  if (targetFilter.value && !params.has('target')) params.set('target', targetFilter.value)
  if (statusFilter.value) params.set('status', statusFilter.value)
  params.set('page', String(targetPage))
  params.set('size', String(targetSize))
  return params.toString()
}

function currentTargetId(): number {
  // 告警/恢复接口按 entry_id 归属对象；列表按对象名过滤，这里从当前行反查 id
  const match = rows.value.find((row) => row['监控对象'] === targetFilter.value)
  return match ? Number(match.id) : NaN
}

async function reloadList(targetPage?: number) {
  errorMessage.value = ''
  infoMessage.value = ''
  const nextPage = targetPage ?? pageInput.value
  const nextSize = sizeInput.value
  // 前端先拦一层非法值，后端仍会再次明确 400，双端口径一致
  if (!Number.isInteger(nextPage) || nextPage < 1) {
    errorMessage.value = `页码必须是不小于 1 的正整数，当前值：${String(nextPage)}，请求已拒绝（不会当成健康空页）`
    rows.value = []
    emptyCode.value = ''
    return
  }
  if (!Number.isInteger(nextSize) || nextSize < 1) {
    errorMessage.value = `每页条数必须是不小于 1 的正整数，当前值：${String(nextSize)}，请求已拒绝（不会当成健康空页）`
    rows.value = []
    emptyCode.value = ''
    return
  }
  try {
    const response = await request(`${ENDPOINT}?${buildListQuery(nextPage, nextSize)}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail ? `请求被拒绝（${response.status}）：${payload.detail}` : `质量指标列表读取失败（${response.status}）`)
    }
    const payload = (await response.json()) as PagePayload
    // 成功响应才覆盖列表，失败时清空旧空态、绝不沿用上一次结果
    rows.value = payload.items ?? []
    total.value = payload.total ?? 0
    page.value = payload.page
    size.value = payload.size
    pageInput.value = payload.page
    sizeInput.value = payload.size
    emptyCode.value = payload.empty_code ?? ''
    infoMessage.value = payload.message ?? ''
  } catch (error) {
    // 400/网络失败：清空列表与空态标记，避免旧数据残留被误读为健康
    rows.value = []
    total.value = 0
    emptyCode.value = ''
    infoMessage.value = ''
    errorMessage.value = error instanceof Error ? error.message : '质量监控列表读取失败'
  }
}

async function reloadSummary() {
  try {
    const response = await request(`${ENDPOINT}/monitor-state`)
    if (response.ok) {
      summary.value = (await response.json()) as Summary
    }
  } catch {
    // 汇总失败不拖垮列表，卡片保持 0 并由列表错误提示兜底
  }
}

async function reloadTargets() {
  try {
    const response = await request(`${ENDPOINT}/targets`)
    if (response.ok) {
      const payload = (await response.json()) as { items: string[] }
      targets.value = payload.items ?? []
    }
  } catch {
    targets.value = []
  }
}

async function reloadAlerts() {
  try {
    const targetId = currentTargetId()
    // 已选监控对象但列表中找不到该对象时，宁可留空也不能回退展示全局告警
    if (targetFilter.value && !Number.isFinite(targetId)) {
      alerts.value = []
      return
    }
    const params = new URLSearchParams()
    if (Number.isFinite(targetId)) params.set('entry_id', String(targetId))
    if (activeOnly.value) params.set('active_only', 'true')
    params.set('page', '1')
    params.set('size', '200')
    const suffix = params.toString() ? `?${params.toString()}` : ''
    const response = await request(`${ENDPOINT}/alerts${suffix}`)
    if (!response.ok) {
      throw new Error(`告警清单读取失败（${response.status}）`)
    }
    const payload = (await response.json()) as PagePayload
    alerts.value = (payload.items ?? []) as AlertRow[]
  } catch (error) {
    alerts.value = []
    errorMessage.value = error instanceof Error ? error.message : '告警清单读取失败'
  }
}

async function reloadRecoveries() {
  try {
    const targetId = currentTargetId()
    if (targetFilter.value && !Number.isFinite(targetId)) {
      recoveries.value = []
      return
    }
    const suffix = Number.isFinite(targetId) ? `?entry_id=${targetId}` : ''
    const response = await request(`${ENDPOINT}/recoveries${suffix}`)
    if (!response.ok) {
      throw new Error(`恢复记录读取失败（${response.status}）`)
    }
    const payload = (await response.json()) as { items: RecoveryRow[] }
    recoveries.value = payload.items ?? []
  } catch (error) {
    recoveries.value = []
    errorMessage.value = error instanceof Error ? error.message : '恢复记录读取失败'
  }
}

async function refreshAll() {
  // 同一份后端状态：先拉列表拿到对象与 id，再同步告警/恢复/汇总
  await reloadList()
  await Promise.all([reloadAlerts(), reloadRecoveries(), reloadSummary()])
}

function resetFilters() {
  filters.value = {}
  targetFilter.value = ''
  statusFilter.value = ''
  activeOnly.value = false
  pageInput.value = 1
  sizeInput.value = 20
  void refreshAll()
}

// 切换监控对象：先清空上一个对象的全部结论，再按新对象加载，杜绝继承健康态
function onTargetChange() {
  rows.value = []
  alerts.value = []
  recoveries.value = []
  total.value = 0
  emptyCode.value = ''
  errorMessage.value = ''
  infoMessage.value = ''
  pageInput.value = 1
  void refreshAll()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function exportAllReceipts() {
  window.open(`${ENDPOINT}/receipts/export`, '_blank')
}

function exportRowReceipts(row: Row) {
  window.open(`${ENDPOINT}/receipts/export?entry_id=${encodeURIComponent(String(row.id))}`, '_blank')
}

function openCreate() {
  errorMessage.value = '质量指标登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '质量监控动作未生效，请稍后重试')
    }
    infoMessage.value = payload.message
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '质量监控操作失败'
  }
}

function openDetection(row: Row) {
  detectionRow.value = row
  detectionResult.value = '成功'
  detectionReason.value = ''
  detectionOpen.value = true
}

async function submitDetection() {
  if (!detectionRow.value) return
  if (detectionResult.value !== '成功' && !detectionReason.value.trim()) {
    errorMessage.value = '检测失败或中断必须填写原因，不能留空提交'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${detectionRow.value.id}/detections`, {
      method: 'POST',
      body: JSON.stringify({ result: detectionResult.value, reason: detectionReason.value }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '检测请求未生效')
    }
    detectionOpen.value = false
    infoMessage.value = payload.message
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '质量检测提交失败'
  }
}

async function recoverRow(row: Row) {
  const reason = window.prompt('请输入该监控对象的告警恢复原因', '重试检测通过，确认恢复')
  if (reason === null) return
  try {
    const response = await request(`${ENDPOINT}/${row.id}/recover`, {
      method: 'POST',
      body: JSON.stringify({ values: { reason } }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '恢复操作未生效')
    }
    infoMessage.value = payload.message
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警恢复失败'
  }
}

async function recoverAlert(alertId: string) {
  const reason = window.prompt(`请输入告警 ${alertId} 的恢复原因`, '人工确认恢复')
  if (reason === null) return
  try {
    const response = await request(`${ENDPOINT}/alerts/${encodeURIComponent(alertId)}/recover`, {
      method: 'POST',
      body: JSON.stringify({ values: { reason } }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '告警恢复未生效')
    }
    infoMessage.value = payload.message
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警恢复失败'
  }
}

onMounted(() => {
  void reloadTargets()
  void refreshAll()
})
</script>

<style scoped>
.state-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  border: 1px solid var(--border);
  background: #f1f5f9;
  color: var(--muted);
}
.tone-ok { color: #067647; }
.tone-fail { color: #b42318; }
.tone-warn { color: #b54708; }
.tone-running { color: #1f6feb; }
.tone-muted { color: var(--muted); }
.info-text { color: #067647; }
.panel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }
.side-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.panel-head h3 { font-size: 14px; margin: 0; }
.panel-check { font-size: 12px; color: var(--muted); }
.data-table.compact th, .data-table.compact td { padding: 6px 8px; font-size: 12px; }
.pager { display: flex; gap: 6px; }
.pager .btn:disabled { opacity: 0.5; cursor: not-allowed; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal-card {
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  width: 460px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.modal-card textarea { width: 100%; resize: vertical; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 1100px) {
  .panel-grid { grid-template-columns: 1fr; }
}
</style>
