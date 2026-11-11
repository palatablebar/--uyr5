import { computed, onBeforeUnmount, ref } from 'vue'
import { useProfileStore } from '../stores/profileStore'
import { RUN_TERMINAL_STATUSES } from '../constants/ruleRunStatus'
import type { ProfilePayload } from '../types/profile'

function isTerminal(status?: string | null) {
  return !!status && (RUN_TERMINAL_STATUSES as string[]).includes(status)
}

export function useProfileQuery() {
  const store = useProfileStore()
  const payload = ref<ProfilePayload | null>(null)
  const error = ref('')
  const loading = computed(() => store.loading)
  const recomputing = computed(() => store.recomputing)
  let timer: ReturnType<typeof setTimeout> | null = null
  // true while the latest attempt is queued/running — a batch in flight never
  // disturbs the effective results on screen, we just poll until it settles
  let inFlight = false

  function stopTimer() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  async function load(datasetId: number, force = false): Promise<ProfilePayload | null> {
    error.value = ''
    stopTimer()
    try {
      payload.value = await store.load(datasetId, force)
      const latest = payload.value.latest_run
      inFlight = !!latest && !isTerminal(latest.status)
      if (inFlight) timer = setTimeout(() => void load(datasetId, true), 1500)
      return payload.value
    } catch (err) {
      error.value = (err as { message?: string }).message ?? '剖析结果加载失败'
      // transient failure while watching a batch: keep polling slowly so the
      // page still refreshes once the batch and the network settle
      if (inFlight) timer = setTimeout(() => void load(datasetId, true), 4000)
      return null
    }
  }

  async function recompute(datasetId: number) {
    error.value = ''
    try {
      const result = await store.recompute(datasetId)
      await load(datasetId, true)
      return result
    } catch (err) {
      error.value = (err as { message?: string }).message ?? '剖析任务提交失败'
      throw err
    }
  }

  onBeforeUnmount(stopTimer)
  return { payload, loading, recomputing, error, load, recompute }
}
