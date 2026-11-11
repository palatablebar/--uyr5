export interface TopValue {
  value: string
  count: number
}

export interface PercentilePositions {
  p50: number | null
  p95: number | null
}

export interface ColumnProfile {
  id: number
  dataset_id: number
  run_id: number | null
  column_name: string
  ordinal: number
  inferred_type: string
  type_label: string
  null_rate: number
  null_rate_display: string
  distinct_count: number
  distinct_display: string
  min_value: string | null
  max_value: string | null
  p50: number | null
  p95: number | null
  p50_display: string
  p95_display: string
  top_values: TopValue[]
  pattern_summary: string | null
  outlier_count: number
  percentile_positions: PercentilePositions
  is_numeric: boolean
  created_at: string | null
}

export interface ProfilePayload {
  dataset: {
    id: number
    name: string
    source_format: string
    row_count: number
    column_count: number
    checksum: string
    status: string
    uploaded_at: string | null
  }
  /** 当前生效批次（columns 所属批次）；首次剖析成功前为 null */
  run: {
    run_id: number
    status: string
    started_at: string | null
    finished_at: string | null
    duration_ms: number
    rows_scanned: number
  } | null
  /** 最近一次剖析尝试（可能仍在执行或已失败），与生效批次无关 */
  latest_run: {
    run_id: number
    status: string
    error_code: string | null
    started_at: string | null
    finished_at: string | null
  } | null
  /** 生效批次相对上一批次的列变化；老数据无对比基线时为 null */
  column_changes: ColumnChanges | null
  columns: ColumnProfile[]
  recommendations: RuleSuggestion[]
}

export interface ColumnChanges {
  previous_run_id: number | null
  added: string[]
  removed: string[]
  type_changed: Array<{ column_name: string; from: string; to: string }>
}

export interface RuleSuggestion {
  rule_type: string
  column_name: string
  params: Record<string, unknown>
  severity: string
  reason: string
}
