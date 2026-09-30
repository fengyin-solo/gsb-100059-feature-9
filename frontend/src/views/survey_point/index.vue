<template>
  <section class="page console" data-module="survey_point">
    <header class="page-head">
      <div>
        <h2>坐标核验台</h2>
        <p class="page-desc">
          围绕图幅与控制点做在线核验：坐标缺失、点类型异常、服务超时分别走不同处置路径；
          核验结论统一刷入控制点台账、点位图与高程坐标核验清单。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openRegister">补测登记控制点</button>
        <button class="btn" type="button" @click="openOfflineMerge">离线采集合并</button>
        <button class="btn ghost" type="button" @click="exportRows">导出控制点台账</button>
      </div>
    </header>

    <!-- 项目坐标规范：X/Y 精度取舍口径 -->
    <div class="spec-strip">
      <span>坐标依据：{{ board.spec['名称'] }}</span>
      <span>坐标X/Y 取舍：保留 {{ board.spec['坐标X小数位'] }} 位（四舍五入）</span>
      <span>高程保留：{{ board.spec['高程小数位'] }} 位</span>
      <span>允许点类型：{{ (board.spec['允许点类型'] ?? []).join('、') }}</span>
    </div>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card" :class="item.tone">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="resetAndReload">
      <label class="filter-item">
        <span>图幅编号</span>
        <select v-model="sheetNo">
          <option value="">全部图幅</option>
          <option v-for="sheet in board.sheets" :key="sheet.图幅编号" :value="sheet.图幅编号">
            {{ sheet.图幅编号 }}{{ sheet.图幅名称 ? ` · ${sheet.图幅名称}` : '' }}
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>点号检索</span>
        <input v-model="keyword" placeholder="按点号检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 空图幅：给可执行的补点入口，而不是一片空态 -->
    <div v-if="board.empty_sheet" class="empty-sheet-banner">
      <div>
        <strong>图幅 {{ sheetNo }} 还没有控制点</strong>
        <p>野外控制尚未起测。可直接按图幅补测登记第一个控制点；同一点号重复提交不会重复登记。</p>
      </div>
      <button class="btn primary" type="button" @click="openRegister">为该图幅补点</button>
    </div>

    <!-- 旧控制点缺点类型：回填点类型并迁移责任组 -->
    <div v-if="legacyRows.length" class="legacy-banner">
      <div class="legacy-head">
        <strong>旧控制点待回填点类型（{{ legacyRows.length }}）</strong>
        <span>启动时已统一迁移到「{{ board.spec['默认责任组'] }}」，请人工回填规范点类型后重新核验。</span>
      </div>
      <ul class="legacy-list">
        <li v-for="row in legacyRows" :key="row.id">
          <span>{{ row.图幅编号 }} / {{ row.点号 }}</span>
          <span class="muted">观测日期：{{ row.观测日期 || '—' }}</span>
          <button class="link" type="button" @click="openBackfill(row)">回填点类型</button>
        </li>
      </ul>
    </div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 控制点台账 -->
    <div v-show="activeTab === 'ledger'">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in ledgerColumns" :key="column">{{ column }}</th>
            <th>核验结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ledgerRows" :key="String(row.id)">
            <td v-for="column in ledgerColumns" :key="column">{{ formatCell(column, cellValue(row, column)) }}</td>
            <td>
              <span class="badge" :class="conclusionClass(row.核验结论)">{{ row.核验结论 }}</span>
              <span v-if="row.已签发" class="issued-tag">已签发 {{ row.签发批次 }}</span>
            </td>
          </tr>
          <tr v-if="!ledgerRows.length">
            <td :colspan="ledgerColumns.length + 1" class="empty-state">
              当前条件下没有控制点，请先补测登记
            </td>
          </tr>
        </tbody>
      </table>
      <div class="pager">
        <span>已载入 {{ ledgerRows.length }} / {{ board.ledger.total }} 条（游标分页，新增点不重不漏）</span>
        <button
          v-if="board.ledger.has_more"
          class="btn"
          type="button"
          :disabled="loadingMore"
          @click="loadMore"
        >
          {{ loadingMore ? '载入中…' : '载入下一页' }}
        </button>
      </div>
    </div>

    <!-- 点位图 -->
    <div v-show="activeTab === 'map'" class="map-panel">
      <div class="map-frame">
        <svg viewBox="0 0 100 100" class="map-svg">
          <line x1="8" y1="92" x2="92" y2="92" class="axis" />
          <line x1="8" y1="92" x2="8" y2="8" class="axis" />
          <text x="90" y="97" class="axis-label">坐标X →</text>
          <text x="2" y="10" class="axis-label">↑ 坐标Y</text>
          <g v-for="point in board.projections.points" :key="point.id">
            <circle
              :cx="point.x"
              :cy="100 - point.y"
              r="2.6"
              :class="['map-dot', conclusionClass(point.核验结论), { issued: point.已签发 }]"
            >
              <title>{{ point.点号 }}（{{ point.点类型 || '待回填' }}）：{{ point.核验结论 }}</title>
            </circle>
            <text :x="point.x + 1.2" :y="100 - point.y - 1.2" class="map-label">{{ point.点号 }}</text>
          </g>
        </svg>
        <div v-if="!board.projections.points.length" class="map-empty">
          当前图幅暂无可上图坐标，先补测登记并完成核验
        </div>
      </div>
      <aside class="map-side">
        <h4>图例与未上图点</h4>
        <ul class="legend">
          <li><i class="dot pass"></i>通过</li>
          <li><i class="dot missing"></i>待补坐标</li>
          <li><i class="dot badtype"></i>待校正点类型</li>
          <li><i class="dot timeout"></i>超时待重试</li>
          <li><i class="dot pending"></i>待核验</li>
          <li><i class="dot ring"></i>外圈金边＝已签发</li>
        </ul>
        <p v-if="!board.projections.missing.length" class="muted">没有缺坐标、未上图的控制点。</p>
        <ul v-else class="missing-list">
          <li v-for="item in board.projections.missing" :key="item.id">
            <span>{{ item.点号 }}</span>
            <button
              class="link"
              type="button"
              @click="openSupplement(verificationById(item.id)!)"
            >
              {{ item.原因 }} · 去补录
            </button>
          </li>
        </ul>
      </aside>
    </div>

    <!-- 高程坐标核验清单 -->
    <div v-show="activeTab === 'checklist'">
      <table class="data-table">
        <thead>
          <tr>
            <th>图幅/点号</th>
            <th>点类型</th>
            <th>坐标X / 坐标Y / 高程</th>
            <th>核验结论</th>
            <th>核验问题</th>
            <th>处置路径</th>
            <th>核验时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in board.verification" :key="`v-${row.id}`">
            <td>
              <div>{{ row.点号 }}</div>
              <div class="muted">{{ row.图幅编号 }}</div>
            </td>
            <td>
              {{ row.点类型 }}
              <span v-if="row.待回填点类型" class="warn-tag">待回填</span>
            </td>
            <td class="coord-cell">
              <div>X：{{ fmtCoord(row.坐标X) }}</div>
              <div>Y：{{ fmtCoord(row.坐标Y) }}</div>
              <div>H：{{ fmtCoord(row.高程) }}</div>
            </td>
            <td>
              <span class="badge" :class="conclusionClass(row.核验结论)">{{ row.核验结论 }}</span>
              <div v-if="row.已签发" class="issued-tag">已签发</div>
            </td>
            <td class="issue-cell">
              <p v-for="(issue, index) in row.核验问题" :key="index" class="issue-line">
                <em>{{ issue.类别 }}</em>：{{ issue.说明 }}
              </p>
              <span v-if="!row.核验问题.length" class="muted">—</span>
            </td>
            <td class="disposition-cell">{{ row.处置建议 }}</td>
            <td>{{ row.核验时间 || '—' }}</td>
            <td class="row-actions vertical">
              <button v-if="canVerify(row)" class="link" type="button" @click="runVerify(row, false)">
                {{ row.核验结论 === '超时待重试' ? '重新核验' : '发起在线核验' }}
              </button>
              <button v-if="row.核验结论 === '待补坐标'" class="link" type="button" @click="openSupplement(row)">
                补录坐标
              </button>
              <button
                v-if="row.核验结论 === '待校正点类型' && !row.待回填点类型"
                class="link"
                type="button"
                @click="openFixType(row)"
              >
                校正点类型
              </button>
              <button v-if="row.待回填点类型" class="link" type="button" @click="openBackfill(row)">
                回填点类型
              </button>
              <button class="link" type="button" @click="openRevise(row)">坐标修订</button>
              <button v-if="row.已签发" class="link" type="button" @click="openReissue(row)">
                重新签发
              </button>
              <button
                v-if="row.核验结论 === '待核验' || row.核验结论 === '超时待重试'"
                class="link drill"
                type="button"
                :disabled="submitting"
                @click="runVerify(row, true)"
              >
                演练服务超时
              </button>
            </td>
          </tr>
          <tr v-if="!board.verification.length">
            <td colspan="8" class="empty-state">当前条件下没有核验记录</td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="page-foot">
      <span>台账、点位图、核验清单取自同一份核验数据，刷新后结论一致</span>
      <span v-if="notice" class="notice-text">{{ notice }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 通用表单弹层：登记 / 补录 / 校正点类型 / 回填 / 修订 / 重新签发 -->
    <div v-if="modal.open" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <header class="modal-head">
          <h3>{{ modal.title }}</h3>
          <button class="link" type="button" @click="closeModal">关闭</button>
        </header>
        <p v-if="modal.hint" class="modal-hint">{{ modal.hint }}</p>

        <form v-if="modal.type !== 'offline'" class="modal-form" @submit.prevent="submitModal">
          <template v-for="field in modal.fields" :key="field.name">
            <label v-if="field.type !== 'select'" class="form-row">
              <span>{{ field.label }}<i v-if="field.required">*</i></span>
              <input
                v-model="modal.form[field.name]"
                :type="field.type === 'number' ? 'number' : 'text'"
                :step="field.type === 'number' ? '0.0001' : undefined"
                :placeholder="field.placeholder"
              />
            </label>
            <label v-else class="form-row">
              <span>{{ field.label }}<i>*</i></span>
              <select v-model="modal.form[field.name]">
                <option value="" disabled>请选择规范点类型</option>
                <option v-for="option in board.spec['允许点类型']" :key="option" :value="option">
                  {{ option }}
                </option>
              </select>
            </label>
          </template>
          <p v-if="modal.error" class="error-text">{{ modal.error }}</p>
          <div class="modal-actions">
            <button class="btn ghost" type="button" @click="closeModal">取消</button>
            <button class="btn primary" type="submit" :disabled="submitting">
              {{ submitting ? '提交中…' : '提交' }}
            </button>
          </div>
        </form>

        <!-- 离线采集合并 -->
        <form v-else class="modal-form" @submit.prevent="submitOfflineMerge">
          <p class="modal-hint">
            粘贴离线采集点（JSON 数组，需含图幅编号、点号）。按点号幂等合并：同点号重复提交只更新差异字段，
            已签发坐标受保护不覆盖。
          </p>
          <textarea v-model="offlineText" class="offline-input" rows="10" />
          <div class="offline-toolbar">
            <button class="btn ghost" type="button" @click="fillOfflineSample">填入演示数据</button>
          </div>
          <p v-if="modal.error" class="error-text">{{ modal.error }}</p>
          <div v-if="offlineSummary" class="offline-result">
            <div class="summary-line">
              收到 {{ offlineSummary.received }} 条：
              新增 {{ offlineSummary.inserted }}，
              更新 {{ offlineSummary.updated }}，
              一致未动 {{ offlineSummary.unchanged }}，
              已签发保护跳过 {{ offlineSummary.skipped_protected }}
            </div>
            <ul>
              <li v-for="(detail, index) in offlineSummary.details" :key="index">
                {{ detail.图幅编号 || '' }} {{ detail.点号 }} —— {{ detail.结果 }}
              </li>
            </ul>
          </div>
          <div class="modal-actions">
            <button class="btn ghost" type="button" @click="closeModal">关闭</button>
            <button class="btn primary" type="submit" :disabled="submitting">
              {{ submitting ? '合并中…' : '幂等合并' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/survey_point'

type Conclusion = '通过' | '待补坐标' | '待校正点类型' | '超时待重试' | '待核验'

interface Issue {
  类别: string
  说明: string
}

interface PointRow {
  id: number
  点号: string
  图幅编号: string
  点类型: string
  坐标X: number | null
  坐标Y: number | null
  高程: number | null
  精度等级: string
  观测日期: string
  status: string
  责任组: string
  来源: string
  已签发: boolean
  签发批次: string | null
  签发日期: string | null
  待回填点类型: boolean
  核验结论: Conclusion
  核验问题: Issue[]
  核验时间: string | null
  处置建议: string
}

interface VerificationRow extends PointRow {}

interface ProjectionPoint {
  id: number
  点号: string
  点类型: string
  x: number
  y: number
  核验结论: Conclusion
  已签发: boolean
}

interface CoordSpec {
  名称: string
  坐标X小数位: number
  坐标Y小数位: number
  高程小数位: number
  允许点类型: string[]
  默认责任组: string
}

interface BoardResponse {
  spec: CoordSpec
  sheets: { 图幅编号: string; 图幅名称: string }[]
  stats: Record<string, number>
  empty_sheet: boolean
  ledger: {
    items: PointRow[]
    total: number
    cursor: number
    next_cursor: number
    has_more: boolean
  }
  projections: {
    points: ProjectionPoint[]
    missing: { id: number; 点号: string; 原因: string }[]
  }
  verification: VerificationRow[]
}

interface ActionResult {
  ok: boolean
  message: string
  entry: PointRow | null
}

interface ModalField {
  name: string
  label: string
  required?: boolean
  type?: 'text' | 'number' | 'select'
  placeholder?: string
}

type ModalType =
  | 'register'
  | 'supplement'
  | 'fix-type'
  | 'backfill'
  | 'revise'
  | 'reissue'
  | 'offline'

interface ModalState {
  open: boolean
  type: ModalType
  title: string
  hint: string
  targetId: number | null
  actionPath: string
  fields: ModalField[]
  form: Record<string, string>
  error: string
}

const emptyBoard: BoardResponse = {
  spec: {
    名称: '项目坐标规范',
    坐标X小数位: 3,
    坐标Y小数位: 3,
    高程小数位: 3,
    允许点类型: [],
    默认责任组: '',
  },
  sheets: [],
  stats: {},
  empty_sheet: false,
  ledger: { items: [], total: 0, cursor: 0, next_cursor: 0, has_more: false },
  projections: { points: [], missing: [] },
  verification: [],
}

const board = reactive<BoardResponse>(emptyBoard)
const ledgerRows = ref<PointRow[]>([])
const sheetNo = ref('')
const keyword = ref('')
const activeTab = ref<'ledger' | 'map' | 'checklist'>('ledger')
const errorMessage = ref('')
const notice = ref('')
const submitting = ref(false)
const loadingMore = ref(false)
const offlineText = ref('')
const offlineSummary = ref<{
  received: number
  inserted: number
  updated: number
  unchanged: number
  skipped_protected: number
  details: { 点号: string; 图幅编号?: string; 结果: string }[]
} | null>(null)

const tabs = [
  { key: 'ledger' as const, label: '控制点台账' },
  { key: 'map' as const, label: '点位图' },
  { key: 'checklist' as const, label: '高程坐标核验清单' },
]

const ledgerColumns = [
  '图幅编号',
  '点号',
  '点类型',
  '坐标X',
  '坐标Y',
  '高程',
  '精度等级',
  '责任组',
  '观测日期',
  'status',
]

const modal = reactive<ModalState>({
  open: false,
  type: 'register',
  title: '',
  hint: '',
  targetId: null,
  actionPath: '',
  fields: [],
  form: {},
  error: '',
})

const statCards = computed(() => [
  { label: '控制点总数', value: board.stats.total ?? 0, tone: '' },
  { label: '待补坐标', value: board.stats.待补坐标 ?? 0, tone: 'tone-missing' },
  { label: '点类型异常', value: board.stats.待校正点类型 ?? 0, tone: 'tone-badtype' },
  { label: '超时待重试', value: board.stats.超时待重试 ?? 0, tone: 'tone-timeout' },
  { label: '旧点待回填类型', value: board.stats.待回填点类型 ?? 0, tone: 'tone-legacy' },
  { label: '已签发', value: board.stats.已签发 ?? 0, tone: 'tone-issued' },
])

const legacyRows = computed(() => board.verification.filter((row) => row.待回填点类型))

function verificationById(id: number): VerificationRow | undefined {
  return board.verification.find((row) => row.id === id)
}

function conclusionClass(conclusion: string): string {
  return {
    通过: 'pass',
    待补坐标: 'missing',
    待校正点类型: 'badtype',
    超时待重试: 'timeout',
    待核验: 'pending',
  }[conclusion] ?? 'pending'
}

function canVerify(row: VerificationRow): boolean {
  return row.核验结论 === '待核验' || row.核验结论 === '超时待重试'
}

function fmtCoord(value: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—（缺失）'
  }
  return value.toFixed(board.spec.坐标X小数位)
}

function cellValue(row: PointRow, column: string): string | number | null {
  const record = row as unknown as Record<string, string | number | null>
  const value = record[column]
  return value ?? null
}

function formatCell(column: string, value: unknown): string {
  if (column === '坐标X' || column === '坐标Y' || column === '高程') {
    return typeof value === 'number' ? value.toFixed(board.spec.坐标X小数位) : '—'
  }
  if (column === 'status') {
    return value === undefined || value === null || value === '' ? '—' : String(value)
  }
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function resetFilters() {
  sheetNo.value = ''
  keyword.value = ''
  void resetAndReload()
}

function buildBoardQuery(cursor: number): string {
  const params = new URLSearchParams()
  if (sheetNo.value) params.set('sheet', sheetNo.value)
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  params.set('cursor', String(cursor))
  params.set('size', '10')
  return `${ENDPOINT}/board?${params.toString()}`
}

async function resetAndReload() {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(buildBoardQuery(0))
    if (!response.ok) {
      throw new Error(`核验台数据读取失败（${response.status}）`)
    }
    const payload = (await response.json()) as BoardResponse
    Object.assign(board, payload)
    ledgerRows.value = payload.ledger.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核验台数据读取失败'
  }
}

async function loadMore() {
  if (loadingMore.value || !board.ledger.has_more) {
    return
  }
  loadingMore.value = true
  try {
    const response = await request(buildBoardQuery(board.ledger.next_cursor))
    if (!response.ok) {
      throw new Error('下一页读取失败，请稍后重试')
    }
    const payload = (await response.json()) as BoardResponse
    Object.assign(board.stats, payload.stats)
    board.projections = payload.projections
    board.verification = payload.verification
    board.ledger = payload.ledger
    // 游标分页本就不重叠，再按 id 去一道，防止翻页期间新增点造成重复
    const seen = new Set(ledgerRows.value.map((row) => row.id))
    ledgerRows.value = [
      ...ledgerRows.value,
      ...payload.ledger.items.filter((row) => !seen.has(row.id)),
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下一页读取失败'
  } finally {
    loadingMore.value = false
  }
}

async function runVerify(row: VerificationRow, simulateTimeout: boolean) {
  errorMessage.value = ''
  notice.value = ''
  submitting.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/verify`, {
      method: 'POST',
      body: JSON.stringify({ simulate_timeout: simulateTimeout }),
    })
    const result = (await response.json()) as ActionResult
    if (!response.ok || !result.ok) {
      throw new Error(result.message || '核验请求未生效')
    }
    notice.value = simulateTimeout
      ? '核验服务超时：坐标与登记均未改动，请直接「重新核验」，不要重新登记点号'
      : result.message
    await resetAndReload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核验失败'
  } finally {
    submitting.value = false
  }
}

// ----------------------------------------------------------------------
// 弹层定义
// ----------------------------------------------------------------------
const FIELD_PRESETS: Record<Exclude<ModalType, 'offline'>, {
  title: string
  hint: string
  path: (id: number | null) => string
  fields: ModalField[]
}> = {
  register: {
    title: '补测登记控制点',
    hint: '按「图幅编号 + 点号」幂等登记：网络重试或重复提交不会产生第二笔。坐标可先缺，后续走补录。',
    path: () => `${ENDPOINT}/register`,
    fields: [
      { name: '图幅编号', label: '图幅编号', required: true, placeholder: '如 I-49-66' },
      { name: '点号', label: '点号', required: true, placeholder: '如 CP01' },
      { name: '点类型', label: '点类型（可后补）', type: 'select' },
      { name: '坐标X', label: '坐标X（米，可选）', type: 'number' },
      { name: '坐标Y', label: '坐标Y（米，可选）', type: 'number' },
      { name: '高程', label: '高程（米，可选）', type: 'number' },
      { name: '精度等级', label: '精度等级', placeholder: '如图根级 / E级' },
    ],
  },
  supplement: {
    title: '坐标缺失 · 补录坐标',
    hint: '补录后按项目坐标规范自动取舍 X/Y 精度并重新核验，无需重新登记点号。',
    path: (id) => `${ENDPOINT}/${id}/supplement`,
    fields: [
      { name: '坐标X', label: '坐标X（米）', required: true, type: 'number' },
      { name: '坐标Y', label: '坐标Y（米）', required: true, type: 'number' },
      { name: '高程', label: '高程（米，缺失可留空）', type: 'number' },
    ],
  },
  'fix-type': {
    title: '点类型异常 · 校正点类型',
    hint: '只能校正为项目坐标规范允许的点类型，校正后自动重新核验。',
    path: (id) => `${ENDPOINT}/${id}/fix-type`,
    fields: [
      { name: '点类型', label: '正确点类型', required: true, type: 'select' },
      { name: '责任组', label: '责任组（可选）', placeholder: '如 控制测量二组' },
    ],
  },
  backfill: {
    title: '旧控制点 · 回填点类型并迁移责任组',
    hint: '旧点已由系统迁移到默认责任组；回填规范点类型后可指定新责任组并重新核验。',
    path: (id) => `${ENDPOINT}/${id}/backfill`,
    fields: [
      { name: '点类型', label: '回填点类型', required: true, type: 'select' },
      { name: '责任组', label: '迁移到责任组', placeholder: '缺省为默认责任组' },
    ],
  },
  revise: {
    title: '坐标普通修订',
    hint: 'X/Y 按项目坐标规范取舍。已签发坐标会被拒绝并提示走「重新签发」，历史成果不会被覆盖。',
    path: (id) => `${ENDPOINT}/${id}/revise`,
    fields: [
      { name: '坐标X', label: '坐标X（米）', required: true, type: 'number' },
      { name: '坐标Y', label: '坐标Y（米）', required: true, type: 'number' },
      { name: '高程', label: '高程（米，可选）', type: 'number' },
      { name: 'remark', label: '修订说明', placeholder: '如 外业复测平差更新' },
    ],
  },
  reissue: {
    title: '已签发坐标 · 重新签发',
    hint: '重新签发会保留历史批次留痕，旧坐标仍可在修订记录中追溯。',
    path: (id) => `${ENDPOINT}/${id}/reissue`,
    fields: [
      { name: '坐标X', label: '新坐标X（米）', required: true, type: 'number' },
      { name: '坐标Y', label: '新坐标Y（米）', required: true, type: 'number' },
      { name: '高程', label: '新高程（米，可选）', type: 'number' },
      { name: '签发批次', label: '签发批次（留空自动编号）', placeholder: '如 PC-20260930-03' },
      { name: 'remark', label: '签发说明', placeholder: '如 起算点更新后重新平差' },
    ],
  },
}

function openModal(
  type: Exclude<ModalType, 'offline'>,
  row?: VerificationRow,
  overrides: Record<string, string> = {},
) {
  const preset = FIELD_PRESETS[type]
  Object.assign(modal, {
    open: true,
    type,
    title: preset.title,
    hint: preset.hint,
    targetId: row?.id ?? null,
    actionPath: preset.path(row?.id ?? null),
    fields: preset.fields,
    form: buildInitialForm(preset.fields, row, overrides),
    error: '',
  })
  offlineSummary.value = null
}

function buildInitialForm(
  fields: ModalField[],
  row: VerificationRow | undefined,
  overrides: Record<string, string>,
): Record<string, string> {
  const form: Record<string, string> = {}
  for (const field of fields) {
    if (field.name in overrides) {
      form[field.name] = overrides[field.name]
    } else if (field.name === '图幅编号') {
      form[field.name] = sheetNo.value
    } else if (row) {
      const current = cellValue(row, field.name)
      form[field.name] = current === null || current === undefined ? '' : String(current)
    } else {
      form[field.name] = ''
    }
  }
  return form
}

function openRegister() {
  openModal('register', undefined, { 图幅编号: sheetNo.value })
}

function openSupplement(row: VerificationRow) {
  openModal('supplement', row)
}

function openFixType(row: VerificationRow) {
  openModal('fix-type', row)
}

function openBackfill(row: VerificationRow) {
  openModal('backfill', row, {
    责任组: board.spec.默认责任组,
  })
}

function openRevise(row: VerificationRow) {
  openModal('revise', row)
}

function openReissue(row: VerificationRow) {
  openModal('reissue', row)
}

function openOfflineMerge() {
  Object.assign(modal, {
    open: true,
    type: 'offline' as ModalType,
    title: '离线采集 · 幂等合并',
    hint: '',
    targetId: null,
    actionPath: `${ENDPOINT}/offline-merge`,
    fields: [],
    form: {},
    error: '',
  })
}

function closeModal() {
  modal.open = false
  modal.error = ''
  offlineSummary.value = null
}

function fillOfflineSample() {
  const sample = [
    { 图幅编号: 'I-49-28', 点号: 'WN03', 高程: 1530.118, 备注: '与台账一致，应判为未改动' },
    { 图幅编号: 'I-49-12', 点号: 'CP01', 坐标X: 1.0, 坐标Y: 2.0, 备注: '已签发，坐标受保护' },
    { 图幅编号: 'I-49-66', 点号: 'KG01', 点类型: 'GPS控制点', 坐标X: 4200111.2, 坐标Y: 701222.3, 高程: 1602.4 },
    { 图幅编号: 'I-49-66', 点号: 'KG01', 坐标X: 4200111.2, 坐标Y: 701222.3, 高程: 1602.4, 备注: '重复一包，幂等不新增' },
  ]
  offlineText.value = JSON.stringify(sample, null, 2)
  modal.error = ''
}

async function submitModal() {
  modal.error = ''
  const missing = modal.fields
    .filter((field) => field.required && !modal.form[field.name]?.trim())
    .map((field) => field.label.replace('（米）', '').replace('*', ''))
  if (missing.length) {
    modal.error = `请先填写：${missing.join('、')}`
    return
  }
  submitting.value = true
  try {
    const values: Record<string, string> = {}
    for (const field of modal.fields) {
      const text = modal.form[field.name]?.trim() ?? ''
      if (text) values[field.name] = text
    }
    const response = await request(modal.actionPath, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const result = (await response.json()) as ActionResult
    if (!response.ok || !result.ok) {
      modal.error = result.message || '操作未生效，请稍后重试'
      return
    }
    notice.value = result.message
    closeModal()
    await resetAndReload()
  } catch (error) {
    modal.error = error instanceof Error ? error.message : '操作失败，请检查服务后重试'
  } finally {
    submitting.value = false
  }
}

async function submitOfflineMerge() {
  modal.error = ''
  let items: unknown
  try {
    items = JSON.parse(offlineText.value || '[]')
  } catch {
    modal.error = 'JSON 格式不正确，请检查采集包内容'
    return
  }
  if (!Array.isArray(items)) {
    modal.error = '采集包必须是点位数组'
    return
  }
  submitting.value = true
  try {
    const response = await request(modal.actionPath, {
      method: 'POST',
      body: JSON.stringify({ items }),
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '离线合并失败')
    }
    offlineSummary.value = payload
    notice.value = '离线采集已按点号幂等合并'
    await resetAndReload()
  } catch (error) {
    modal.error = error instanceof Error ? error.message : '离线合并失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

onMounted(resetAndReload)
</script>

<style scoped>
.console .spec-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  background: #eef4ff;
  border: 1px solid #c7dcff;
  color: #1e3a6b;
  font-size: 12px;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
}
.stat-card.tone-missing { border-left: 4px solid #d97706; }
.stat-card.tone-badtype { border-left: 4px solid #ea580c; }
.stat-card.tone-timeout { border-left: 4px solid #dc2626; }
.stat-card.tone-legacy { border-left: 4px solid #7c3aed; }
.stat-card.tone-issued { border-left: 4px solid #16a34a; }
.filter-item select { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; }
.empty-sheet-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: #fff7ed;
  border: 1px dashed #d97706;
  border-radius: 8px;
  padding: 14px 18px;
  margin-bottom: 12px;
}
.empty-sheet-banner p { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
.legacy-banner {
  background: #f5f3ff;
  border: 1px solid #c4b5fd;
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 12px;
}
.legacy-head { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.legacy-head span { color: var(--muted); font-size: 12px; }
.legacy-list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 18px; }
.legacy-list li { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.tabs { display: flex; gap: 4px; margin-bottom: 0; }
.tab {
  border: 1px solid var(--border);
  border-bottom: none;
  background: #f1f5f9;
  border-radius: 8px 8px 0 0;
  padding: 8px 16px;
  cursor: pointer;
  font-size: 13px;
}
.tab.active { background: #fff; font-weight: 600; color: var(--brand); }
.badge {
  display: inline-block;
  border-radius: 10px;
  padding: 2px 10px;
  font-size: 12px;
  white-space: nowrap;
}
.badge.pass { background: #dcfce7; color: #166534; }
.badge.missing { background: #fef3c7; color: #92400e; }
.badge.badtype { background: #ffedd5; color: #9a3412; }
.badge.timeout { background: #fee2e2; color: #991b1b; }
.badge.pending { background: #e2e8f0; color: #475569; }
.issued-tag { display: inline-block; margin-left: 6px; color: #15803d; font-size: 11px; }
.warn-tag {
  display: inline-block;
  margin-left: 6px;
  background: #ede9fe;
  color: #6d28d9;
  border-radius: 8px;
  padding: 0 6px;
  font-size: 11px;
}
.muted { color: var(--muted); font-size: 12px; }
.pager { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
.map-panel { display: flex; gap: 14px; background: #fff; border: 1px solid var(--border); padding: 12px; }
.map-frame { flex: 1; min-height: 420px; position: relative; display: flex; justify-content: center; }
.map-svg { width: auto; height: 420px; aspect-ratio: 1 / 1; border: 1px solid var(--border); background: #fbfdff; }
.axis { stroke: #94a3b8; stroke-width: 0.4; }
.axis-label { font-size: 3.4px; fill: #64748b; }
.map-dot { stroke: #fff; stroke-width: 0.3; }
.map-dot.pass { fill: #16a34a; }
.map-dot.missing { fill: #d97706; }
.map-dot.badtype { fill: #ea580c; }
.map-dot.timeout { fill: #dc2626; }
.map-dot.pending { fill: #94a3b8; }
.map-dot.issued { stroke: #b8860b; stroke-width: 0.9; }
.map-label { font-size: 3px; fill: #334155; }
.map-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--muted); }
.map-side { width: 240px; border-left: 1px solid var(--border); padding-left: 12px; }
.map-side h4 { margin: 0 0 8px; font-size: 13px; }
.legend { list-style: none; margin: 0 0 12px; padding: 0; font-size: 12px; display: flex; flex-direction: column; gap: 4px; }
.legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.legend .dot.pass { background: #16a34a; }
.legend .dot.missing { background: #d97706; }
.legend .dot.badtype { background: #ea580c; }
.legend .dot.timeout { background: #dc2626; }
.legend .dot.pending { background: #94a3b8; }
.legend .dot.ring { background: #fff; border: 2px solid #b8860b; width: 8px; height: 8px; }
.missing-list { list-style: none; margin: 0; padding: 0; font-size: 12px; display: flex; flex-direction: column; gap: 4px; }
.coord-cell { font-variant-numeric: tabular-nums; white-space: nowrap; }
.issue-cell { max-width: 260px; }
.issue-line { margin: 0 0 2px; font-size: 12px; }
.issue-line em { font-style: normal; font-weight: 600; color: #b42318; }
.disposition-cell { max-width: 220px; font-size: 12px; color: #334155; }
.row-actions.vertical { flex-direction: column; align-items: flex-start; gap: 4px; }
.link.drill { color: #b45309; }
.notice-text { color: #15803d; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal { background: #fff; border-radius: 10px; width: 480px; max-width: 92vw; max-height: 88vh; overflow: auto; padding: 18px 20px; }
.modal-head { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; font-size: 16px; }
.modal-hint { font-size: 12px; color: var(--muted); background: #f8fafc; border-radius: 6px; padding: 8px 10px; }
.modal-form { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
.form-row { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.form-row i { color: #dc2626; font-style: normal; margin-left: 2px; }
.form-row input, .form-row select, .offline-input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
}
.offline-input { width: 100%; font-family: monospace; resize: vertical; }
.offline-toolbar { display: flex; justify-content: flex-end; }
.offline-result { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 8px 10px; font-size: 12px; }
.offline-result .summary-line { font-weight: 600; margin-bottom: 6px; }
.offline-result ul { margin: 0; padding-left: 18px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
button:disabled { opacity: 0.55; cursor: not-allowed; }
</style>
