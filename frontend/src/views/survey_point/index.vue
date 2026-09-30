<template>
  <section class="page console" data-module="survey_point">
    <header class="page-head">
      <div>
        <h2>坐标核验台</h2>
        <p class="page-desc">
          围绕图幅与控制点做坐标核验：缺坐标补测、点类型异常回填、服务超时转离线；
          核验结论统一刷入台账、点位图与高程坐标核验清单。
          坐标取舍以项目规范为准（X/Y {{ coordSpec.X }} 位、高程 {{ coordSpec['高程'] }} 位，四舍五入）。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openRegister()">补点 / 登记控制点</button>
        <button class="btn" type="button" @click="offlineOpen = true">离线采集夹（{{ offlineClip.length }}）</button>
        <button class="btn ghost" type="button" @click="runLegacyMigrate">历史点治理</button>
        <button class="btn ghost" type="button" @click="exportRows">导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article
        v-for="item in statCards"
        :key="item.label"
        class="stat-card"
        :class="{ clickable: !!item.filter }"
        @click="item.filter && applyQuickFilter(item.filter)"
      >
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.tone">{{ item.value }}</strong>
      </article>
    </div>

    <!-- 服务超时面板：与缺坐标、点类型异常是三条不同处置路径 -->
    <div v-if="serviceState === 'timeout'" class="alert alert-timeout" role="alert">
      <div>
        <strong>核验服务响应超时（超过 8 秒）</strong>
        <p>当前结论可能不是最新，点位图与清单保持上一次成功读取的数据，不做本地猜测。写操作的结果无法确认时，系统不会替你重复提交。</p>
      </div>
      <div class="alert-actions">
        <button class="btn" type="button" :disabled="loading" @click="refreshAll">只读重试刷新</button>
        <button class="btn primary" type="button" @click="offlineOpen = true">转入离线采集夹</button>
        <button class="btn ghost" type="button" @click="serviceState = 'ok'; errorMessage = ''">先看上一次数据</button>
      </div>
    </div>
    <div v-else-if="serviceState === 'error'" class="alert alert-error" role="alert">
      <div>
        <strong>核验服务暂时不可用</strong>
        <p>{{ errorMessage || '请求未送达，请检查网络后重试。' }}</p>
      </div>
      <div class="alert-actions">
        <button class="btn" type="button" :disabled="loading" @click="refreshAll">重新连接</button>
        <button class="btn primary" type="button" @click="offlineOpen = true">转入离线采集夹</button>
      </div>
    </div>

    <!-- 图幅选择 + 空图幅补点入口 -->
    <div class="sheet-bar">
      <button
        class="sheet-chip"
        :class="{ active: !selectedSheet }"
        type="button"
        @click="selectSheet('')"
      >
        全部图幅
      </button>
      <button
        v-for="sheet in sheets"
        :key="sheet['图幅编号']"
        class="sheet-chip"
        :class="{ active: selectedSheet === sheet['图幅编号'], empty: sheet['控制点数'] === 0 }"
        type="button"
        @click="selectSheet(sheet['图幅编号'])"
      >
        {{ sheet['图幅编号'] }}
        <span class="sheet-name">{{ sheet['图幅名称'] || '未命名图幅' }}</span>
        <span class="sheet-count">{{ sheet['控制点数'] }} 点 · 待核验 {{ sheet['待核验'] }}</span>
      </button>
    </div>

    <div v-if="activeSheetEmpty" class="empty-sheet">
      <div>
        <strong>图幅 {{ selectedSheet }} 还没有控制点</strong>
        <p>空图幅不能只留一片空态：先补点再核验。补点登记按点号幂等，网络抖动重试也不会重复登记同一点号。</p>
      </div>
      <button class="btn primary" type="button" @click="openRegister(selectedSheet)">为该图幅补第一个控制点</button>
    </div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
      <span class="tab-hint">三处视图共用同一份核验数据，结论刷新后保持一致</span>
    </div>

    <!-- 点位图 -->
    <div v-show="activeTab === 'map'" class="panel">
      <div v-if="!mappablePoints.length" class="inline-empty">
        当前图幅没有可上图的点（平面坐标缺失）。
        <button class="link" type="button" @click="openRegister(selectedSheet)">去补点</button>
      </div>
      <div v-else class="map-wrap">
        <svg class="point-map" viewBox="0 0 760 420" role="img" aria-label="控制点点位图">
          <line v-for="gx in gridXs" :key="'gx' + gx" :x1="gx" y1="20" :x2="gx" y2="400" class="grid" />
          <line v-for="gy in gridYs" :key="'gy' + gy" x1="40" :y1="gy" x2="740" :y2="gy" class="grid" />
          <g v-for="p in mappablePoints" :key="String(p.id)">
            <circle
              :cx="projectX(p['坐标X'])"
              :cy="projectY(p['坐标Y'])"
              r="8"
              class="map-dot"
              :class="verdictClass(p)"
              @click="openDetail(p.id)"
            />
            <text
              v-if="p['签发坐标']"
              :x="projectX(p['坐标X']) - 3"
              :y="projectY(p['坐标Y']) - 11"
              class="lock-glyph"
            >🔒</text>
            <text
              :x="projectX(p['坐标X']) + 11"
              :y="projectY(p['坐标Y']) + 4"
              class="map-label"
              @click="openDetail(p.id)"
            >{{ p['点号'] }}</text>
          </g>
        </svg>
        <ul class="map-legend">
          <li><span class="dot good"></span>合格</li>
          <li><span class="dot pending"></span>待核验 / 待复测</li>
          <li><span class="dot bad"></span>不合格</li>
          <li>🔒 已签发（普通修订锁定）</li>
        </ul>
      </div>
      <ul v-if="unmappablePoints.length" class="unmappable">
        <li v-for="p in unmappablePoints" :key="String(p.id)">
          <span class="badge danger">坐标缺失</span>
          {{ p['点号'] }}（{{ p['图幅编号'] || '未挂图幅' }}）无法上图 ——
          <button class="link" type="button" @click="openDetail(p.id)">补测坐标</button>
        </li>
      </ul>
    </div>

    <!-- 高程坐标核验清单 -->
    <div v-show="activeTab === 'checklist'" class="panel">
      <table class="data-table">
        <thead>
          <tr>
            <th>点号</th><th>图幅</th><th>点类型</th><th>坐标X</th><th>坐标Y</th><th>高程</th>
            <th>异常标记</th><th>核验结论</th><th>处置</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in checklist" :key="String(p.id)">
            <td>{{ p['点号'] }}{{ p['已签发'] ? ' 🔒' : '' }}</td>
            <td>{{ p['图幅编号'] || '—' }}</td>
            <td :class="{ 'cell-warn': p['点类型异常'] }">{{ p['点类型'] || '—' }}</td>
            <td :class="{ 'cell-warn': p['坐标缺失'] }">{{ fmt(p['坐标X']) }}</td>
            <td :class="{ 'cell-warn': p['坐标缺失'] }">{{ fmt(p['坐标Y']) }}</td>
            <td :class="{ 'cell-warn': p['高程缺失'] }">{{ fmt(p['高程']) }}</td>
            <td>
              <span v-if="p['坐标缺失']" class="badge danger">坐标缺失</span>
              <span v-if="p['高程缺失']" class="badge warn">高程缺失</span>
              <span v-if="p['点类型异常']" class="badge danger">点类型异常</span>
              <span v-if="!p['坐标缺失'] && !p['高程缺失'] && !p['点类型异常']" class="badge ok">数据齐</span>
            </td>
            <td><span class="verdict" :class="verdictClass(p)">{{ p['核验结论'] }}</span></td>
            <td class="row-actions">
              <button class="link" type="button" @click="openDetail(p.id)">核验处置</button>
            </td>
          </tr>
          <tr v-if="!checklist.length">
            <td colspan="9" class="empty-state">
              该图幅暂无控制点 —
              <button class="link" type="button" @click="openRegister(selectedSheet)">立即补点</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 控制点台账（服务端稳定分页） -->
    <div v-show="activeTab === 'ledger'" class="panel">
      <form class="filter-bar" @submit.prevent="reloadLedger">
        <label class="filter-item">
          <span>点号</span>
          <input v-model="filters.keyword" placeholder="按点号检索" />
        </label>
        <label class="filter-item">
          <span>核验结论</span>
          <select v-model="filters.verdict">
            <option value="">全部</option>
            <option v-for="v in verdictOptions" :key="v" :value="v">{{ v }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>异常类型</span>
          <select v-model="filters.anomaly">
            <option value="">全部</option>
            <option value="坐标缺失">坐标缺失</option>
            <option value="点类型异常">点类型异常</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetLedgerFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in ledgerColumns" :key="column">{{ column }}</th>
            <th>核验</th>
            <th>点位动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ledgerRows" :key="String(row.id)">
            <td>{{ row['点号'] }}{{ row['签发坐标'] ? ' 🔒' : '' }}</td>
            <td>{{ row['图幅编号'] || '—' }}</td>
            <td :class="{ 'cell-warn': row['点类型异常'] }">{{ row['点类型'] || '—' }}</td>
            <td :class="{ 'cell-warn': row['坐标缺失'] }">{{ fmt(row['坐标X']) }}</td>
            <td :class="{ 'cell-warn': row['坐标缺失'] }">{{ fmt(row['坐标Y']) }}</td>
            <td :class="{ 'cell-warn': row['高程缺失'] }">{{ fmt(row['高程']) }}</td>
            <td>{{ row['精度等级'] || '—' }}</td>
            <td>{{ row['观测日期'] || '—' }}</td>
            <td>{{ row['责任组'] || '—' }}</td>
            <td><span class="verdict" :class="verdictClass(row)">{{ row['核验结论'] || '待核验' }}</span></td>
            <td class="row-actions">
              <button class="link" type="button" @click="openDetail(row.id)">核验处置</button>
              <button class="link" type="button" @click="openDetail(row.id, 'revise')">修订</button>
            </td>
            <td class="row-actions">
              <button
                v-for="action in pointActions"
                :key="action"
                class="link"
                type="button"
                @click="runPointAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!ledgerRows.length">
            <td :colspan="ledgerColumns.length + 2" class="empty-state">
              当前筛选下没有控制点 —
              <button class="link" type="button" @click="openRegister(selectedSheet)">补一个控制点</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="pager">
        <button class="btn" type="button" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</button>
        <span>第 {{ page }} / {{ totalPages }} 页，共 {{ ledgerTotal }} 条（按登记顺序稳定分页）</span>
        <button class="btn" type="button" :disabled="page >= totalPages || loading" @click="changePage(page + 1)">下一页</button>
      </div>
    </div>

    <footer class="page-foot">
      <span>{{ flash || (errorMessage ? '' : '结论变更后台账、点位图、核验清单统一刷新') }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记 / 补点弹窗 -->
    <div v-if="registerOpen" class="modal-mask" @click.self="registerOpen = false">
      <div class="modal">
        <h3>登记控制点（按点号幂等）</h3>
        <p class="modal-tip">点号已存在时只返回既有记录，失败重试不会重复登记。坐标将按规范自动取舍。</p>
        <div class="form-grid">
          <label><span>点号 *</span><input v-model="regForm['点号']" placeholder="如 CP08" /></label>
          <label>
            <span>图幅编号 *</span>
            <input v-model="regForm['图幅编号']" list="sheet-options" placeholder="选择或输入图幅编号" />
            <datalist id="sheet-options">
              <option v-for="s in sheets" :key="s['图幅编号']" :value="s['图幅编号']">{{ s['图幅名称'] }}</option>
            </datalist>
          </label>
          <label>
            <span>点类型</span>
            <select v-model="regForm['点类型']">
              <option value="">暂不确定（留空将进入点类型异常处置）</option>
              <option v-for="t in pointTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
          <label class="filter-item"><span>精度等级</span><input v-model="regForm['精度等级']" /></label>
          <label>
            <span>坐标X（原始值）</span>
            <input v-model="regForm['坐标X']" placeholder="将取舍为 3 位小数" />
            <small class="round-preview">落库：{{ roundPreview(regForm['坐标X'], coordSpec.X) }}</small>
          </label>
          <label>
            <span>坐标Y（原始值）</span>
            <input v-model="regForm['坐标Y']" placeholder="将取舍为 3 位小数" />
            <small class="round-preview">落库：{{ roundPreview(regForm['坐标Y'], coordSpec.Y) }}</small>
          </label>
          <label>
            <span>高程（原始值）</span>
            <input v-model="regForm['高程']" placeholder="将取舍为 4 位小数" />
            <small class="round-preview">落库：{{ roundPreview(regForm['高程'], coordSpec['高程']) }}</small>
          </label>
          <label class="filter-item"><span>观测日期</span><input v-model="regForm['观测日期']" type="date" /></label>
        </div>
        <div v-if="registerNote" class="alert alert-info">{{ registerNote }}</div>
        <div v-if="registerTimeout" class="alert alert-timeout">
          服务超时：无法确认登记是否成功。<strong>请勿换个入口再点一次</strong>，
          用同一点号重试即可，后端按点号幂等，不会重复登记。
          <button class="btn" type="button" :disabled="busyKeys.has('register')" @click="submitRegister">用同一点号重试</button>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="registerOpen = false">取消</button>
          <button class="btn primary" type="button" :disabled="busyKeys.has('register')" @click="submitRegister">
            {{ busyKeys.has('register') ? '提交中…' : '登记' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 核验处置弹窗：三类异常走三条不同路径 -->
    <div v-if="detailPoint" class="modal-mask" @click.self="detailPoint = null">
      <div class="modal modal-wide">
        <h3>
          核验处置 · {{ detailPoint['点号'] }}
          <span v-if="detailPoint['签发坐标']" class="badge ok">已签发锁定</span>
        </h3>

        <div v-if="detailPoint['坐标缺失']" class="alert alert-danger">
          <strong>坐标缺失</strong>：平面 X/Y 不齐全，不能判合格、不能签发。
          <button class="btn primary" type="button" @click="detailMode = 'revise'">走补测坐标路径</button>
        </div>
        <div v-if="detailPoint['点类型异常']" class="alert alert-danger">
          <strong>点类型异常</strong>：当前「{{ detailPoint['点类型'] || '空' }}」不在项目点类型目录。
          <label class="inline-field">
            回填为
            <select v-model="backfillType">
              <option v-for="t in pointTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
          <button class="btn primary" type="button" :disabled="busyKeys.has('backfill')" @click="submitBackfill">
            回填点类型并迁移责任组
          </button>
        </div>
        <div v-if="detailPoint['高程缺失']" class="alert alert-warn">
          高程缺失：平面可先核验，但高程坐标核验清单会持续挂账，建议联测水准后补录。
          <button class="btn" type="button" @click="detailMode = 'revise'">补录高程</button>
        </div>

        <dl class="point-facts">
          <div><dt>图幅</dt><dd>{{ detailPoint['图幅编号'] || '—' }}</dd></div>
          <div><dt>当前结论</dt><dd>{{ detailPoint['核验结论'] || '待核验' }}</dd></div>
          <div><dt>X / Y</dt><dd>{{ fmt(detailPoint['坐标X']) }} / {{ fmt(detailPoint['坐标Y']) }}</dd></div>
          <div><dt>高程</dt><dd>{{ fmt(detailPoint['高程']) }}</dd></div>
          <div><dt>责任组</dt><dd>{{ detailPoint['责任组'] || '—' }}</dd></div>
          <div v-if="detailPoint['结论说明']"><dt>说明</dt><dd>{{ detailPoint['结论说明'] }}</dd></div>
          <div v-if="detailPoint['签发坐标']">
            <dt>签发快照</dt>
            <dd>X={{ detailPoint['签发坐标'].X }}，Y={{ detailPoint['签发坐标'].Y }}，高程={{ detailPoint['签发坐标']['高程'] }}</dd>
          </div>
        </dl>

        <!-- 修订 / 补测 / 重新签发 -->
        <div v-if="detailMode === 'revise'" class="sub-panel">
          <h4>{{ detailPoint['签发坐标'] ? '重新签发（覆盖已签发坐标的唯一路径）' : '补测 / 修订（按规范取舍）' }}</h4>
          <div class="form-grid">
            <label>
              <span>坐标X</span>
              <input v-model="revForm['坐标X']" :disabled="false" placeholder="留空表示不改" />
              <small class="round-preview">落库：{{ roundPreview(revForm['坐标X'], coordSpec.X) }}</small>
            </label>
            <label>
              <span>坐标Y</span>
              <input v-model="revForm['坐标Y']" placeholder="留空表示不改" />
              <small class="round-preview">落库：{{ roundPreview(revForm['坐标Y'], coordSpec.Y) }}</small>
            </label>
            <label>
              <span>高程</span>
              <input v-model="revForm['高程']" placeholder="留空表示不改" />
              <small class="round-preview">落库：{{ roundPreview(revForm['高程'], coordSpec['高程']) }}</small>
            </label>
            <label>
              <span>点类型</span>
              <select v-model="revForm['点类型']">
                <option value="">不改</option>
                <option v-for="t in pointTypes" :key="t" :value="t">{{ t }}</option>
              </select>
            </label>
            <label class="filter-item"><span>精度等级</span><input v-model="revForm['精度等级']" /></label>
            <label class="filter-item"><span>观测日期</span><input v-model="revForm['观测日期']" type="date" /></label>
            <label class="filter-item">
              <span>责任组</span>
              <input v-model="revForm['责任组']" placeholder="如 测绘控制组" />
            </label>
            <label v-if="detailPoint['签发坐标']" class="filter-item">
              <span>新签发批次 *</span>
              <input v-model="reissueBatch" placeholder="如 B-2026-10" />
            </label>
          </div>
          <p v-if="detailPoint['签发坐标']" class="modal-tip warn-text">
            重新签发后结论强制转「待复测」，需复测复核；原快照被新快照替换并留痕。
          </p>
          <div v-if="reviseTimeout" class="alert alert-timeout">
            服务超时：写操作结果未确认，已阻止重复提交。
            <button class="btn" type="button" @click="retryDetailWrite">用相同内容重试</button>
          </div>
          <div class="modal-actions">
            <button class="btn ghost" type="button" @click="detailMode = 'inspect'">收起</button>
            <button
              v-if="!detailPoint['签发坐标']"
              class="btn"
              type="button"
              :disabled="busyKeys.has('revise')"
              @click="submitRevise"
            >保存修订</button>
            <button
              v-else
              class="btn primary"
              type="button"
              :disabled="busyKeys.has('reissue')"
              @click="submitReissue"
            >确认重新签发</button>
          </div>
        </div>
        <button v-else class="btn" type="button" @click="detailMode = 'revise'">
          {{ detailPoint['坐标缺失'] || detailPoint['高程缺失'] ? '补测 / 补录坐标' : '修订坐标或属性' }}
        </button>

        <!-- 下核验结论 -->
        <div class="sub-panel">
          <h4>核验结论（合格前自动校验坐标与点类型）</h4>
          <textarea v-model="verifyNote" rows="2" placeholder="结论说明（可选）"></textarea>
          <div v-if="verifyTimeout" class="alert alert-timeout">
            服务超时：结论是否落库未知，已阻止重复提交。
            <button class="btn" type="button" @click="retryDetailWrite">重试同一条结论</button>
          </div>
          <div class="modal-actions">
            <button
              class="btn primary"
              type="button"
              :disabled="busyKeys.has('verify') || detailPoint['坐标缺失'] || detailPoint['点类型异常']"
              @click="submitVerify('合格')"
            >判合格</button>
            <button class="btn" type="button" :disabled="busyKeys.has('verify')" @click="submitVerify('不合格')">判不合格</button>
            <button class="btn" type="button" :disabled="busyKeys.has('verify')" @click="submitVerify('待复测')">转待复测</button>
            <button
              v-if="!detailPoint['签发坐标']"
              class="btn ghost"
              type="button"
              :disabled="busyKeys.has('issue') || detailPoint['核验结论'] !== '合格'"
              @click="submitIssue"
            >签发坐标</button>
          </div>
        </div>

        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="detailPoint = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- 离线采集夹：中断后按点号幂等合并 -->
    <div v-if="offlineOpen" class="modal-mask" @click.self="offlineOpen = false">
      <div class="modal modal-wide">
        <h3>离线采集夹</h3>
        <p class="modal-tip">
          服务超时或野外断网时先存这里；恢复后一键合并：按点号幂等，只补空字段，
          已签发坐标不会被覆盖。同一批重复提交结果一致，可安全重试。
        </p>
        <div class="form-grid">
          <label class="filter-item"><span>点号</span><input v-model="offlineDraft['点号']" /></label>
          <label class="filter-item"><span>图幅编号</span><input v-model="offlineDraft['图幅编号']" /></label>
          <label class="filter-item"><span>X</span><input v-model="offlineDraft['坐标X']" /></label>
          <label class="filter-item"><span>Y</span><input v-model="offlineDraft['坐标Y']" /></label>
          <label class="filter-item"><span>高程</span><input v-model="offlineDraft['高程']" /></label>
          <label class="filter-item"><span>点类型</span>
            <select v-model="offlineDraft['点类型']">
              <option value="">留空</option>
              <option v-for="t in pointTypes" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="addOfflinePoint">加入采集夹</button>
          <button class="btn ghost" type="button" @click="simulateInterrupt">模拟中断重开（内容不丢）</button>
        </div>

        <table class="data-table">
          <thead><tr><th>点号</th><th>图幅</th><th>X</th><th>Y</th><th>高程</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(p, idx) in offlineClip" :key="idx">
              <td>{{ p['点号'] }}</td><td>{{ p['图幅编号'] || '—' }}</td>
              <td>{{ p['坐标X'] || '—' }}</td><td>{{ p['坐标Y'] || '—' }}</td><td>{{ p['高程'] || '—' }}</td>
              <td><button class="link danger" type="button" @click="offlineClip.splice(idx, 1)">移除</button></td>
            </tr>
            <tr v-if="!offlineClip.length"><td colspan="6" class="empty-state">采集夹为空</td></tr>
          </tbody>
        </table>

        <details class="paste-box">
          <summary>粘贴 JSON 批量导入</summary>
          <textarea v-model="offlinePaste" rows="4" placeholder='[{"点号":"CP02","坐标X":"543.1","坐标Y":"544.2"}]'></textarea>
          <button class="btn" type="button" @click="importOfflineJson">解析并加入</button>
        </details>

        <div v-if="offlineTimeout" class="alert alert-timeout">
          合并请求超时：服务端可能已收到。请直接用同一批点重试 —— 按点号幂等，不会新增重复点。
          <button class="btn" type="button" @click="submitOffline" :disabled="busyKeys.has('offline')">同批重试</button>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="offlineOpen = false">关闭</button>
          <button class="btn primary" type="button" :disabled="!offlineClip.length || busyKeys.has('offline')" @click="submitOffline">
            {{ busyKeys.has('offline') ? '合并中…' : '按点号幂等合并' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Coord = number | string | null
interface ControlPoint {
  id: number
  点号: string
  图幅编号?: string | null
  点类型?: string | null
  坐标X?: Coord
  坐标Y?: Coord
  高程?: Coord
  精度等级?: string | null
  观测日期?: string | null
  责任组?: string | null
  核验结论?: string
  结论说明?: string | null
  坐标缺失?: boolean
  高程缺失?: boolean
  点类型异常?: boolean
  已签发?: boolean
  签发坐标?: { X: number; Y: number; 高程: number | null; 签发批次?: string | null } | null
  status?: string
}
interface Sheet { 图幅编号: string; 图幅名称: string; 控制点数: number; 待核验: number; 异常点: number }
interface ConsolePayload {
  coordSpec: { X: number; Y: number; 高程: number }
  stats: Record<string, number>
  sheets: Sheet[]
  mapPoints: ControlPoint[]
  checklist: ControlPoint[]
}

const ENDPOINT = '/api/survey_point'
const REQUEST_TIMEOUT_MS = 8000

const tabs = [
  { key: 'map', label: '点位图' },
  { key: 'checklist', label: '高程坐标核验清单' },
  { key: 'ledger', label: '控制点台账' },
] as const
type TabKey = (typeof tabs)[number]['key']

const coordSpec = ref({ X: 3, Y: 3, 高程: 4 })
const pointTypes = ref<string[]>([])
const allPoints = ref<ControlPoint[]>([])
const sheets = ref<Sheet[]>([])
const stats = ref<Record<string, number>>({})

const activeTab = ref<TabKey>('map')
const selectedSheet = ref('')
const loading = ref(false)
const serviceState = ref<'ok' | 'timeout' | 'error'>('ok')
const errorMessage = ref('')
const flash = ref('')
const consoleData = ref<ConsolePayload | null>(null)

// 台账（服务端稳定分页）
const ledgerRows = ref<ControlPoint[]>([])
const ledgerTotal = ref(0)
const page = ref(1)
const pageSize = 5
const filters = ref({ keyword: '', verdict: '', anomaly: '' })
const ledgerColumns = ['点号', '图幅', '点类型', '坐标X', '坐标Y', '高程', '精度等级', '观测日期', '责任组', '核验结论']
const pointActions = ['登记损坏', '安排恢复', '标记废弃']
const verdictOptions = ['待核验', '合格', '不合格', '待复测']

// 弹窗与忙状态（忙状态按操作键互斥，杜绝失败后重复点击造成重复登记）
const busyKeys = ref<Set<string>>(new Set())
const registerOpen = ref(false)
const registerTimeout = ref(false)
const registerNote = ref('')
const emptyRegForm = (): Record<string, string> => ({
  点号: '', 图幅编号: '', 点类型: '', 坐标X: '', 坐标Y: '', 高程: '', 精度等级: '', 观测日期: '',
})
const regForm = ref<Record<string, string>>(emptyRegForm())

const detailPoint = ref<ControlPoint | null>(null)
const detailMode = ref<'inspect' | 'revise'>('inspect')
const backfillType = ref('图根点')
const verifyNote = ref('')
const reissueBatch = ref('')
const reviseTimeout = ref(false)
const verifyTimeout = ref(false)
const emptyRevForm = (): Record<string, string> => ({
  坐标X: '', 坐标Y: '', 高程: '', 点类型: '', 精度等级: '', 观测日期: '', 责任组: '',
})
const revForm = ref<Record<string, string>>(emptyRevForm())
let pendingDetailWrite: (() => Promise<void>) | null = null

const offlineOpen = ref(false)
const offlineClip = ref<Array<Record<string, string>>>([])
const offlineDraft = ref<Record<string, string>>({ 点号: '', 图幅编号: '', 坐标X: '', 坐标Y: '', 高程: '', 点类型: '' })
const offlinePaste = ref('')
const offlineTimeout = ref(false)

const visiblePoints = computed(() =>
  selectedSheet.value
    ? allPoints.value.filter((p) => String(p.图幅编号 || '') === selectedSheet.value)
    : allPoints.value,
)
const mappablePoints = computed(() => visiblePoints.value.filter((p) => Number.isFinite(Number(p.坐标X)) && Number.isFinite(Number(p.坐标Y))))
const unmappablePoints = computed(() => visiblePoints.value.filter((p) => !(Number.isFinite(Number(p.坐标X)) && Number.isFinite(Number(p.坐标Y)))))
const checklist = computed(() =>
  selectedSheet.value
    ? (consoleData.value?.checklist ?? []).filter((p) => String(p.图幅编号 || '') === selectedSheet.value)
    : (consoleData.value?.checklist ?? []),
)
const activeSheetEmpty = computed(() => {
  if (!selectedSheet.value) return false
  const sheet = sheets.value.find((s) => s.图幅编号 === selectedSheet.value)
  return !!sheet && sheet.控制点数 === 0
})
const totalPages = computed(() => Math.max(1, Math.ceil(ledgerTotal.value / pageSize)))

const statCards = computed(() => [
  { label: '控制点总数', value: stats.value['控制点总数'] ?? 0, tone: '', filter: null as Record<string, string> | null },
  { label: '待核验 / 待复测', value: stats.value['待核验'] ?? 0, tone: 'tone-warn', filter: { verdict: '待核验' } },
  { label: '核验合格', value: stats.value['合格'] ?? 0, tone: 'tone-good', filter: { verdict: '合格' } },
  { label: '坐标缺失', value: stats.value['坐标缺失'] ?? 0, tone: 'tone-bad', filter: { anomaly: '坐标缺失' } },
  { label: '点类型异常', value: stats.value['点类型异常'] ?? 0, tone: 'tone-bad', filter: { anomaly: '点类型异常' } },
  { label: '已签发锁定', value: stats.value['已签发'] ?? 0, tone: '', filter: null },
])

// 点位图投影
const MAP_BOUNDS = { minX: 40, maxX: 740, minY: 400, maxY: 20 }
const gridXs = [120, 220, 320, 420, 520, 620, 720]
const gridYs = [80, 140, 200, 260, 320, 380]
function coordRange() {
  const xs = mappablePoints.value.map((p) => Number(p.坐标X))
  const ys = mappablePoints.value.map((p) => Number(p.坐标Y))
  const padX = Math.max((Math.max(...xs) - Math.min(...xs)) * 0.1, 1)
  const padY = Math.max((Math.max(...ys) - Math.min(...ys)) * 0.1, 1)
  return { minX: Math.min(...xs) - padX, maxX: Math.max(...xs) + padX, minY: Math.min(...ys) - padY, maxY: Math.max(...ys) + padY }
}
function projectX(value: Coord | undefined) {
  const r = coordRange()
  const t = (Number(value) - r.minX) / (r.maxX - r.minX || 1)
  return MAP_BOUNDS.minX + t * (MAP_BOUNDS.maxX - MAP_BOUNDS.minX)
}
function projectY(value: Coord | undefined) {
  const r = coordRange()
  const t = (Number(value) - r.minY) / (r.maxY - r.minY || 1)
  return MAP_BOUNDS.minY + t * (MAP_BOUNDS.maxY - MAP_BOUNDS.minY)
}

function fmt(value: Coord | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  return Number.isFinite(n) ? String(n) : String(value)
}
function verdictClass(p: ControlPoint) {
  switch (p.核验结论) {
    case '合格': return 'good'
    case '不合格': return 'bad'
    case '待复测': return 'pending'
    default: return 'pending'
  }
}
// 前端取舍只做预览，最终以后端按规范落库为准
function roundPreview(raw: string, digits: number): string {
  if (!raw.trim()) return '（不改 / 缺失）'
  const n = Number(raw)
  if (!Number.isFinite(n)) return '无法解析，将按缺失处理'
  const f = Math.round((n + Number.EPSILON) * 10 ** digits) / 10 ** digits
  return f.toFixed(digits)
}

class TimeoutError extends Error {}

async function timedRequest(path: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    return await request(path, { ...init, signal: controller.signal })
  } catch (error) {
    // request 封装会把 fetch 的 AbortError 包成普通 Error，按消息识别超时
    if ((error instanceof DOMException && error.name === 'AbortError')
      || (error instanceof Error && /abort/i.test(error.message))) {
      throw new TimeoutError('核验服务响应超时')
    }
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}

async function readJson(path: string): Promise<unknown> {
  const response = await timedRequest(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return response.json()
}

function setFlash(message: string) {
  flash.value = message
  window.setTimeout(() => {
    if (flash.value === message) flash.value = ''
  }, 4000)
}

async function refreshAll() {
  loading.value = true
  errorMessage.value = ''
  try {
    const query = selectedSheet.value ? `?sheet=${encodeURIComponent(selectedSheet.value)}` : ''
    const payload = (await readJson(`${ENDPOINT}/console${query}`)) as ConsolePayload
    consoleData.value = payload
    coordSpec.value = payload.coordSpec
    allPoints.value = payload.mapPoints
    sheets.value = payload.sheets
    stats.value = payload.stats
    serviceState.value = 'ok'
  } catch (error) {
    if (error instanceof TimeoutError) {
      serviceState.value = 'timeout'
      errorMessage.value = '只读刷新超时，可重试或转离线采集'
    } else {
      serviceState.value = 'error'
      errorMessage.value = error instanceof Error ? error.message : '核验台数据读取失败'
    }
  } finally {
    loading.value = false
  }
}

async function reloadLedger() {
  loading.value = true
  try {
    const params = new URLSearchParams({ page: String(page.value), size: String(pageSize) })
    if (filters.value.keyword) params.set('keyword', filters.value.keyword)
    if (filters.value.verdict) params.set('verdict', filters.value.verdict)
    if (filters.value.anomaly) params.set('anomaly', filters.value.anomaly)
    if (selectedSheet.value) params.set('sheet', selectedSheet.value)
    const payload = (await readJson(`${ENDPOINT}?${params.toString()}`)) as {
      items: ControlPoint[]
      total: number
    }
    ledgerRows.value = payload.items
    ledgerTotal.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof TimeoutError
      ? '台账分页读取超时，已保留本页数据；请重试，不要重复写操作'
      : error instanceof Error
        ? error.message
        : '台账读取失败'
  } finally {
    loading.value = false
  }
}

async function refreshViews() {
  await Promise.all([refreshAll(), reloadLedger()])
}

function selectSheet(code: string) {
  selectedSheet.value = code
  page.value = 1
  void refreshViews()
}

function changePage(next: number) {
  page.value = Math.min(Math.max(1, next), totalPages.value)
  void reloadLedger()
}

function applyQuickFilter(filter: Record<string, string>) {
  activeTab.value = 'ledger'
  filters.value = { keyword: '', verdict: filter.verdict ?? '', anomaly: filter.anomaly ?? '' }
  page.value = 1
  void reloadLedger()
}

function resetLedgerFilters() {
  filters.value = { keyword: '', verdict: '', anomaly: '' }
  page.value = 1
  void reloadLedger()
}

async function postOnce(key: string, path: string, body: unknown): Promise<{ ok: boolean; message: string; entry?: ControlPoint | Record<string, unknown> }> {
  if (busyKeys.value.has(key)) throw new Error('上一次提交尚未确认，已阻止重复提交')
  busyKeys.value.add(key)
  try {
    const response = await timedRequest(path, { method: 'POST', body: JSON.stringify(body) })
    const payload = (await response.json().catch(() => ({}))) as { ok?: boolean; message?: string; entry?: ControlPoint }
    return { ok: !!payload.ok && response.ok, message: payload.message ?? `接口返回 ${response.status}`, entry: payload.entry }
  } finally {
    busyKeys.value.delete(key)
  }
}

function openRegister(prefillSheet = '') {
  regForm.value = { ...emptyRegForm(), 图幅编号: prefillSheet }
  registerTimeout.value = false
  registerNote.value = ''
  registerOpen.value = true
}

async function submitRegister() {
  registerTimeout.value = false
  if (!regForm.value['点号'].trim() || !regForm.value['图幅编号'].trim()) {
    registerNote.value = '点号与图幅编号为必填'
    return
  }
  const values: Record<string, string> = {}
  for (const [key, value] of Object.entries(regForm.value)) {
    if (value.trim()) values[key] = value.trim()
  }
  try {
    const result = await postOnce('register', `${ENDPOINT}/register`, { values })
    if (!result.ok) {
      registerNote.value = result.message
      return
    }
    registerNote.value = result.message
    setFlash(result.message)
    registerOpen.value = false
    page.value = 1
    await refreshViews()
  } catch (error) {
    if (error instanceof TimeoutError) registerTimeout.value = true
    else registerNote.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function openDetail(id: number, mode: 'inspect' | 'revise' = 'inspect') {
  try {
    const point = (await readJson(`${ENDPOINT}/${id}`)) as ControlPoint
    detailPoint.value = point
    detailMode.value = mode
    revForm.value = emptyRevForm()
    verifyNote.value = point.结论说明 ?? ''
    backfillType.value = pointTypes.value.includes('图根点') ? '图根点' : (pointTypes.value[0] ?? '')
    reissueBatch.value = ''
    reviseTimeout.value = false
    verifyTimeout.value = false
    pendingDetailWrite = null
  } catch (error) {
    errorMessage.value = error instanceof TimeoutError ? '控制点明细读取超时，请只读重试' : '控制点明细读取失败'
  }
}

async function refreshDetail() {
  if (!detailPoint.value) return
  const point = (await readJson(`${ENDPOINT}/${detailPoint.value.id}`)) as ControlPoint
  detailPoint.value = point
}

function nonEmptyReviseValues(): Record<string, string> {
  const values: Record<string, string> = {}
  for (const [key, value] of Object.entries(revForm.value)) {
    if (value.trim()) values[key] = value.trim()
  }
  return values
}

async function submitRevise() {
  if (!detailPoint.value) return
  const id = detailPoint.value.id
  const values = nonEmptyReviseValues()
  reviseTimeout.value = false
  if (!Object.keys(values).length) {
    errorMessage.value = '没有需要保存的修改'
    return
  }
  pendingDetailWrite = async () => {
    const result = await postOnce('revise', `${ENDPOINT}/${id}/revise`, { values })
    if (!result.ok) throw new Error(result.message)
    setFlash(result.message)
    reviseTimeout.value = false
    await refreshDetail()
    await refreshViews()
  }
  try {
    await pendingDetailWrite()
  } catch (error) {
    if (error instanceof TimeoutError) reviseTimeout.value = true
    else errorMessage.value = error instanceof Error ? error.message : '修订失败'
  }
}

async function submitReissue() {
  if (!detailPoint.value) return
  const id = detailPoint.value.id
  const values = nonEmptyReviseValues()
  reviseTimeout.value = false
  if (!reissueBatch.value.trim()) {
    errorMessage.value = '重新签发必须填写新签发批次'
    return
  }
  pendingDetailWrite = async () => {
    const result = await postOnce('reissue', `${ENDPOINT}/${id}/reissue`, { values, batch: reissueBatch.value.trim() })
    if (!result.ok) throw new Error(result.message)
    setFlash(result.message)
    reviseTimeout.value = false
    detailMode.value = 'inspect'
    await refreshDetail()
    await refreshViews()
  }
  try {
    await pendingDetailWrite()
  } catch (error) {
    if (error instanceof TimeoutError) reviseTimeout.value = true
    else errorMessage.value = error instanceof Error ? error.message : '重新签发失败'
  }
}

async function submitBackfill() {
  if (!detailPoint.value) return
  try {
    const result = await postOnce('backfill', `${ENDPOINT}/${detailPoint.value.id}/backfill-type`, {
      point_type: backfillType.value,
      group: '测绘控制组',
    })
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    setFlash(result.message)
    await refreshDetail()
    await refreshViews()
  } catch (error) {
    errorMessage.value = error instanceof TimeoutError ? '回填请求超时，请勿重复点，稍后用相同内容重试' : (error instanceof Error ? error.message : '回填失败')
  }
}

async function submitVerify(verdict: string) {
  if (!detailPoint.value) return
  const id = detailPoint.value.id
  verifyTimeout.value = false
  pendingDetailWrite = async () => {
    const result = await postOnce('verify', `${ENDPOINT}/${id}/verify`, { verdict, note: verifyNote.value })
    if (!result.ok) throw new Error(result.message)
    setFlash(result.message)
    verifyTimeout.value = false
    await refreshDetail()
    await refreshViews()
  }
  try {
    await pendingDetailWrite()
  } catch (error) {
    if (error instanceof TimeoutError) verifyTimeout.value = true
    else errorMessage.value = error instanceof Error ? error.message : '核验失败'
  }
}

async function submitIssue() {
  if (!detailPoint.value) return
  const batch = window.prompt('签发批次号（如 B-2026-09）', '')
  if (batch === null) return
  try {
    const result = await postOnce('issue', `${ENDPOINT}/${detailPoint.value.id}/issue`, { batch })
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    setFlash(result.message)
    await refreshDetail()
    await refreshViews()
  } catch (error) {
    errorMessage.value = error instanceof TimeoutError ? '签发请求超时，结果未确认，请勿重复签发，稍后重试' : (error instanceof Error ? error.message : '签发失败')
  }
}

async function retryDetailWrite() {
  if (pendingDetailWrite) {
    reviseTimeout.value = false
    verifyTimeout.value = false
    try {
      await pendingDetailWrite()
    } catch (error) {
      if (error instanceof TimeoutError) {
        reviseTimeout.value = true
        verifyTimeout.value = true
      } else {
        errorMessage.value = error instanceof Error ? error.message : '重试失败'
      }
    }
  }
}

async function runPointAction(action: string, row: ControlPoint) {
  try {
    const result = await postOnce(`action-${row.id}-${action}`, `${ENDPOINT}/${row.id}/actions`, { values: { action } })
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    setFlash(result.message)
    await refreshViews()
  } catch (error) {
    errorMessage.value = error instanceof TimeoutError ? '点位动作超时，结果未确认，请勿重复点击，稍后用同一动作重试' : (error instanceof Error ? error.message : '操作失败')
  }
}

async function runLegacyMigrate() {
  try {
    const result = await postOnce('migrate', `${ENDPOINT}/migrate-legacy`, {})
    setFlash(result.message)
    if (result.entry && typeof result.entry === 'object') {
      const summary = result.entry as { 回填点类型?: string[]; 迁移责任组?: string[] }
      setFlash(`${result.message}：回填点类型 ${summary.回填点类型?.join('、') || '无'}；迁移责任组 ${summary.迁移责任组?.join('、') || '无'}`)
    }
    await refreshViews()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史点治理失败'
  }
}

// ---- 离线采集夹 ----
function addOfflinePoint() {
  if (!offlineDraft.value['点号'].trim()) {
    errorMessage.value = '离线点至少要有点号，才能按点号幂等合并'
    return
  }
  const point: Record<string, string> = {}
  for (const [key, value] of Object.entries(offlineDraft.value)) {
    if (value.trim()) point[key] = value.trim()
  }
  offlineClip.value.push(point)
  offlineDraft.value = { 点号: '', 图幅编号: '', 坐标X: '', 坐标Y: '', 高程: '', 点类型: '' }
  errorMessage.value = ''
}

function simulateInterrupt() {
  offlineOpen.value = false
  serviceState.value = 'timeout'
  errorMessage.value = '模拟：网络中断。离线采集夹内容保留，恢复后可按点号幂等合并。'
}

function importOfflineJson() {
  try {
    const parsed = JSON.parse(offlinePaste.value) as Array<Record<string, unknown>>
    if (!Array.isArray(parsed)) throw new Error('需要点数组')
    let added = 0
    for (const item of parsed) {
      const code = String(item['点号'] ?? '').trim()
      if (!code) continue
      const point: Record<string, string> = {}
      for (const [key, value] of Object.entries(item)) {
        if (value !== null && value !== undefined && String(value).trim()) point[key] = String(value).trim()
      }
      offlineClip.value.push(point)
      added += 1
    }
    offlinePaste.value = ''
    setFlash(`已从 JSON 加入 ${added} 个离线点`)
  } catch (error) {
    errorMessage.value = `JSON 解析失败：${error instanceof Error ? error.message : '格式不正确'}`
  }
}

async function submitOffline() {
  offlineTimeout.value = false
  try {
    const result = await postOnce('offline', `${ENDPOINT}/sync-offline`, { points: offlineClip.value })
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    const entry = result.entry as { 新增?: number; 合并?: number; 跳过?: number }
    setFlash(`离线合并完成：新增 ${entry.新增 ?? 0}，合并 ${entry.合并 ?? 0}，跳过 ${entry.跳过 ?? 0}`)
    offlineClip.value = []
    offlineOpen.value = false
    page.value = 1
    await refreshViews()
  } catch (error) {
    if (error instanceof TimeoutError) offlineTimeout.value = true
    else errorMessage.value = error instanceof Error ? error.message : '离线合并失败'
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

onMounted(async () => {
  try {
    const types = (await readJson(`${ENDPOINT}/point-types`)) as { items: string[] }
    pointTypes.value = types.items
  } catch {
    pointTypes.value = ['控制点', '图根点', '水准点', 'GPS点', '三角点']
  }
  await Promise.all([refreshAll(), reloadLedger()])
})
</script>

<style scoped>
.console .page-actions { gap: 8px; }
.stat-card.clickable { cursor: pointer; }
.tone-good { color: #067647; }
.tone-warn { color: #b54708; }
.tone-bad { color: #b42318; }

.alert { display: flex; justify-content: space-between; align-items: center; gap: 12px;
  border: 1px solid; border-radius: 8px; padding: 10px 12px; margin: 10px 0; font-size: 13px; }
.alert p { margin: 4px 0 0; color: var(--muted); }
.alert-actions { display: flex; gap: 8px; flex-shrink: 0; }
.alert-timeout { background: #fffaeb; border-color: #fedf89; }
.alert-error, .alert-danger { background: #fef3f2; border-color: #fda29b; }
.alert-warn { background: #fffaeb; border-color: #fedf89; }
.alert-info { background: #eff8ff; border-color: #b2ddff; }

.sheet-bar { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.sheet-chip { display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  border: 1px solid var(--border); background: #fff; border-radius: 8px; padding: 6px 10px; cursor: pointer; font-size: 13px; }
.sheet-chip.active { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand); }
.sheet-chip.empty { border-style: dashed; color: var(--muted); }
.sheet-name { font-size: 11px; color: var(--muted); }
.sheet-count { font-size: 11px; }

.empty-sheet { display: flex; justify-content: space-between; align-items: center; gap: 16px;
  border: 1px dashed #b2ddff; background: #eff8ff; border-radius: 8px; padding: 16px; margin-bottom: 12px; }
.empty-sheet p { margin: 4px 0 0; font-size: 13px; color: var(--muted); }

.tabs { display: flex; align-items: center; gap: 4px; border-bottom: 1px solid var(--border); margin: 8px 0; }
.tab { border: none; background: none; padding: 8px 14px; cursor: pointer; font-size: 14px; color: var(--muted); border-bottom: 2px solid transparent; }
.tab.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }
.tab-hint { margin-left: auto; font-size: 12px; color: var(--muted); }

.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.inline-empty { color: var(--muted); font-size: 13px; padding: 24px; text-align: center; }
.map-wrap { display: flex; gap: 12px; }
.point-map { flex: 1; background: #fbfdff; border: 1px solid var(--border); border-radius: 6px; }
.grid { stroke: #eef2f6; stroke-width: 1; }
.map-dot { stroke: #fff; stroke-width: 1.5; cursor: pointer; }
.map-dot.good { fill: #12b76a; }
.map-dot.pending { fill: #f79009; }
.map-dot.bad { fill: #f04438; }
.map-label { font-size: 11px; fill: #334155; cursor: pointer; }
.lock-glyph { font-size: 10px; }
.map-legend { list-style: none; padding: 0; margin: 0; font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 6px; min-width: 150px; }
.map-legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.dot.good { background: #12b76a; }
.dot.pending { background: #f79009; }
.dot.bad { background: #f04438; }
.unmappable { margin: 10px 0 0; padding-left: 18px; font-size: 13px; color: #b42318; display: flex; flex-direction: column; gap: 4px; }

.badge { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 11px; margin-right: 4px; }
.badge.danger { background: #fee4e2; color: #b42318; }
.badge.warn { background: #fef0c7; color: #b54708; }
.badge.ok { background: #d1fadf; color: #067647; }
.cell-warn { background: #fff4f2; color: #b42318; }
.verdict { font-weight: 600; }
.verdict.good { color: #067647; }
.verdict.pending { color: #b54708; }
.verdict.bad { color: #b42318; }

.pager { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; font-size: 12px; color: var(--muted); }

.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 560px; max-height: 88vh; overflow-y: auto; }
.modal-wide { width: 720px; }
.modal h3 { margin: 0 0 6px; display: flex; gap: 8px; align-items: center; }
.modal h4 { margin: 12px 0 8px; font-size: 14px; }
.modal-tip { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
.warn-text { color: #b54708; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.form-grid label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); }
.form-grid input, .form-grid select, .sub-panel input, .sub-panel select, textarea {
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; }
.round-preview { color: var(--brand); }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
.sub-panel { border-top: 1px dashed var(--border); margin-top: 12px; padding-top: 10px; }
.point-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; font-size: 13px; margin: 10px 0; }
.point-facts dt { color: var(--muted); display: inline; }
.point-facts dd { display: inline; margin: 0 0 0 6px; }
.point-facts > div { display: flex; }
.inline-field { display: inline-flex; align-items: center; gap: 6px; margin: 0 8px; }
.paste-box { margin-top: 10px; font-size: 13px; }
.paste-box textarea { width: 100%; margin: 6px 0; }
.link.danger { color: #b42318; }
</style>
