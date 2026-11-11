<template>
  <div class="paper-page">
    <PageHeader
      eyebrow="assay report"
      :title="payload?.dataset.name ?? '检验报告单'"
      :subtitle="subtitle"
      :meta="meta"
    >
      <template #actions>
        <QuotaMeter class="w-[130px] mr-3" :quota="runStore.quota" />
        <button class="btn" :disabled="!auth.canWrite || recomputing || batchInFlight" @click="recompute">
          {{ batchInFlight ? '剖析执行中…' : '重算剖析' }}
        </button>
        <RouterLink to="/rules" class="btn">去建规则 →</RouterLink>
      </template>
    </PageHeader>

    <ErrorNotice :error="error" retryable @retry="load(Number(route.params.id))" />

    <div v-if="loading && !payload" class="py-10 text-[12.5px] text-ink-soft">正在读取剖析结果…</div>

    <template v-if="payload">
      <section class="mb-4 border border-rule px-4 py-3 flex flex-wrap gap-x-10 gap-y-3">
        <div>
          <div class="eyebrow mb-1">当前生效批次</div>
          <div v-if="payload.run" class="text-[12.5px] tabular-nums">
            #{{ payload.run.run_id }} · 完成于 {{ payload.run.finished_at ?? '-' }}
          </div>
          <div v-if="payload.run" class="text-[10.5px] text-ink-soft tabular-nums">
            扫描 {{ formatInt(payload.run.rows_scanned) }} 行 · 耗时 {{ formatDuration(payload.run.duration_ms) }}
          </div>
          <div v-else class="text-[12.5px] text-ink-soft">尚未有生效批次</div>
        </div>
        <div>
          <div class="eyebrow mb-1">最近处理状态</div>
          <div v-if="payload.latest_run" class="text-[12.5px]">
            <span :style="latestFailed ? { color: 'var(--alert-deep)' } : batchInFlight ? { color: 'var(--assay)' } : undefined">
              {{ statusLabel(payload.latest_run.status) }}
            </span>
            <span class="text-[10.5px] text-ink-soft tabular-nums"> · 批次 #{{ payload.latest_run.run_id }}</span>
          </div>
          <div v-if="batchInFlight" class="text-[10.5px] text-ink-soft">
            新批次整批计算成功后才会生效，期间继续展示当前结果
          </div>
          <div v-else-if="latestFailed" class="text-[10.5px]" :style="{ color: 'var(--alert-deep)' }">
            {{ payload.latest_run?.error_code ?? '' }} · 失败不影响当前生效结果，可重试
          </div>
          <div v-else-if="!payload.latest_run" class="text-[12.5px] text-ink-soft">—</div>
        </div>
        <div class="min-w-[220px] flex-1">
          <div class="eyebrow mb-1">列变化{{ changes?.previous_run_id ? `（较批次 #${changes.previous_run_id}）` : '' }}</div>
          <div class="text-[12.5px]">{{ changeSummary }}</div>
          <div v-if="changeDetails.length" class="mt-0.5 text-[10.5px] text-ink-soft">
            <div v-for="line in changeDetails" :key="line">{{ line }}</div>
          </div>
        </div>
      </section>

      <template v-if="payload.columns.length">
        <div class="flex items-center justify-between mb-3">
          <div class="eyebrow">列级检验记录 · 共 {{ payload.columns.length }} 列</div>
          <div class="text-[11px] text-ink-soft tabular-nums">
            批次 #{{ payload.run?.run_id ?? '-' }} · 扫描 {{ formatInt(payload.run?.rows_scanned ?? 0) }} 行 · 耗时 {{ formatDuration(payload.run?.duration_ms) }}
          </div>
        </div>

        <ColumnTable :columns="payload.columns" @pick="pickColumn" />

        <section v-if="payload.recommendations.length" class="mt-8">
          <div class="flex items-baseline justify-between border-t border-rule pt-3">
            <div class="eyebrow">从剖析结果推荐（当前生效批次）</div>
            <RouterLink to="/rules" class="link-button text-[11px]">全部带入规则构建器 →</RouterLink>
          </div>
          <ul class="mt-2">
            <li v-for="item in payload.recommendations" :key="item.column_name + item.rule_type" class="flex items-baseline gap-4 py-1.5 border-b border-dashed border-rule">
              <span class="text-[12px] w-[120px] tabular-nums">{{ item.column_name }}</span>
              <span class="text-[11.5px] w-[80px]">{{ item.rule_type }}</span>
              <SeverityBadge :severity="item.severity" class="w-[60px]" />
              <span class="text-[11.5px] text-ink-soft">{{ item.reason }}</span>
            </li>
          </ul>
        </section>

        <div class="mt-8 flex items-center justify-between border-t border-rule pt-3 text-[10.5px] text-ink-soft">
          <span>数据集 #{{ payload.dataset.id }} · 校验和 {{ payload.dataset.checksum.slice(0, 16) }}</span>
          <span>生成时间 {{ new Date().toLocaleString('zh-CN') }} · 报告单编号 QC-{{ payload.dataset.id }}-PROFILE</span>
        </div>
        <ActivityTrail />
      </template>

      <EmptyState
        v-else-if="batchInFlight"
        class="mt-6"
        title="剖析批次执行中"
        description="整批计算成功后才会对外生效；本页会在批次结束后自动刷新。"
      />
      <EmptyState
        v-else-if="latestFailed"
        class="mt-6"
        title="最近一次剖析失败"
        :description="`批次 #${payload.latest_run?.run_id} 未能完成（${payload.latest_run?.error_code ?? '未知错误'}），没有可用结果。修复后可重新发起剖析。`"
      >
        <button class="btn btn-solid" :disabled="!auth.canWrite || recomputing" @click="recompute">重新剖析</button>
      </EmptyState>
      <EmptyState
        v-else
        class="mt-6"
        title="这份标本还没验过"
        description="先执行一次剖析：系统会逐列计算空值率、基数、分位、模式与异常值。"
      >
        <button class="btn btn-solid" :disabled="!auth.canWrite || recomputing" @click="recompute">开始剖析</button>
      </EmptyState>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '../components/common/PageHeader.vue'
import ColumnTable from '../components/profile/ColumnTable.vue'
import QuotaMeter from '../components/common/QuotaMeter.vue'
import ErrorNotice from '../components/common/ErrorNotice.vue'
import EmptyState from '../components/common/EmptyState.vue'
import ActivityTrail from '../components/common/ActivityTrail.vue'
import SeverityBadge from '../components/common/SeverityBadge.vue'
import { useProfileQuery } from '../hooks/useProfileQuery'
import { useRunStore } from '../stores/runStore'
import { useAuthStore } from '../stores/authStore'
import { formatDuration, formatInt } from '../utils/formatters'
import { RUN_STATUS_LABELS, RUN_TERMINAL_STATUSES } from '../constants/ruleRunStatus'
import { COLUMN_TYPE_LABELS } from '../constants/columnType'

const route = useRoute()
const router = useRouter()
const runStore = useRunStore()
const auth = useAuthStore()
const { payload, loading, recomputing, error, load, recompute: recomputeProfile } = useProfileQuery()

const datasetId = computed(() => Number(route.params.id))
const subtitle = computed(() => (payload.value ? `${payload.value.dataset.source_format.toUpperCase()} · ${payload.value.dataset.column_count} 列 · 校验和 ${payload.value.dataset.checksum.slice(0, 10)}` : ''))
const meta = computed(() => {
  if (!payload.value) return []
  return [
    { label: '行数', value: formatInt(payload.value.dataset.row_count) },
    { label: '列数', value: String(payload.value.dataset.column_count) },
    { label: '生效批次', value: `#${payload.value.run?.run_id ?? '-'}` },
    { label: '耗时', value: formatDuration(payload.value.run?.duration_ms) },
    { label: '上传时间', value: payload.value.dataset.uploaded_at ?? '-' },
  ]
})

const latestStatus = computed(() => payload.value?.latest_run?.status ?? null)
const batchInFlight = computed(
  () => !!latestStatus.value && !(RUN_TERMINAL_STATUSES as string[]).includes(latestStatus.value),
)
const latestFailed = computed(() => latestStatus.value === 'failed')

const changes = computed(() => payload.value?.column_changes ?? null)
const changeSummary = computed(() => {
  const diff = changes.value
  if (!diff) return payload.value?.run ? '暂无对比基线' : '—'
  if (diff.previous_run_id === null) return `首个生效批次 · 共 ${diff.added.length} 列`
  const total = diff.added.length + diff.removed.length + diff.type_changed.length
  if (!total) return '列结构无变化'
  const parts: string[] = []
  if (diff.added.length) parts.push(`新增 ${diff.added.length} 列`)
  if (diff.removed.length) parts.push(`移除 ${diff.removed.length} 列`)
  if (diff.type_changed.length) parts.push(`类型变化 ${diff.type_changed.length} 列`)
  return parts.join(' · ')
})
const changeDetails = computed(() => {
  const diff = changes.value
  if (!diff || diff.previous_run_id === null) return []
  const lines: string[] = []
  if (diff.added.length) lines.push(`+ ${diff.added.join('、')}`)
  if (diff.removed.length) lines.push(`− ${diff.removed.join('、')}`)
  for (const item of diff.type_changed) {
    lines.push(`~ ${item.column_name}：${COLUMN_TYPE_LABELS[item.from] ?? item.from} → ${COLUMN_TYPE_LABELS[item.to] ?? item.to}`)
  }
  return lines
})

function statusLabel(status: string) {
  return RUN_STATUS_LABELS[status] ?? status
}

onMounted(async () => {
  await load(datasetId.value)
})

watch(datasetId, (id) => load(id))

function pickColumn(columnName: string) {
  router.push({ path: '/rules', query: { dataset: String(datasetId.value), column: columnName } })
}

async function recompute() {
  try {
    await recomputeProfile(datasetId.value)
  } catch {
    /* error ref already carries the envelope */
  }
}
</script>
