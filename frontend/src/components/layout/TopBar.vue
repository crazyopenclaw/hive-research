<template>
  <header class="top-bar">
    <div class="top-bar-left">
      <span class="session-title" :title="question">{{ truncatedQuestion }}</span>
    </div>
    <div class="top-bar-center">
      <div class="experiment-chips">
        <div class="exp-chip">
          <span>Pending</span>
          <strong>{{ counts.pending }}</strong>
        </div>
        <div class="exp-chip running">
          <span>Running</span>
          <strong>{{ counts.running }}</strong>
        </div>
        <div class="exp-chip success">
          <span>Completed</span>
          <strong>{{ counts.completed }}</strong>
        </div>
        <div class="exp-chip danger">
          <span>Failed</span>
          <strong>{{ counts.failed }}</strong>
        </div>
      </div>
    </div>
    <div class="top-bar-right">
      <div class="budget-display" :class="{ warning: budget.is_warning }">
        <div class="budget-bar">
          <div class="budget-fill" :style="{ width: budget.percentage + '%' }" :class="{ warning: budget.percentage >= 90 }"></div>
        </div>
        <span class="dollars" :title="`${budget.tokens_used} tokens used`">
          ${{ budget.dollars_used.toFixed(2) }}<template v-if="budget.dollars_budget"> / ${{ budget.dollars_budget.toFixed(2) }}</template>
        </span>
      </div>
      <div class="spend-item">
        <span class="label">Compute</span>
        <span class="value">${{ computeSpent.toFixed(2) }}</span>
      </div>
      <div class="spend-item">
        <span class="label">Elapsed</span>
        <span class="value">{{ formattedElapsed }}</span>
      </div>
      <div class="actions">
        <button v-if="status === 'active'" class="btn" @click="$emit('pause')">Pause</button>
        <button v-else-if="status === 'paused'" class="btn" @click="$emit('resume')">Resume</button>
        <button class="btn danger" @click="$emit('stop')">Stop</button>
      </div>
    </div>
  </header>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  question: { type: String, default: '' },
  experiments: { type: Object, default: () => ({}) },
  budget: { type: Object, default: () => ({
    tokens_used: 0,
    dollars_used: 0,
    dollars_budget: 0,
    percentage: 0,
    is_warning: false,
  })},
  status: { type: String, default: '' },
  createdAt: { type: String, default: '' },
})

defineEmits(['pause', 'resume', 'stop'])

const truncatedQuestion = computed(() => {
  const q = props.question || 'Research Session'
  return q.length > 60 ? q.slice(0, 57) + '...' : q
})

const counts = computed(() => ({
  pending: props.experiments?.pending || 0,
  running: props.experiments?.running || 0,
  completed: props.experiments?.completed || 0,
  failed: props.experiments?.failed || 0,
}))

const computeSpent = computed(() => Number(props.budget?.compute_spent || 0))

const elapsedSeconds = ref(0)
let elapsedInterval = null

const formattedElapsed = computed(() => {
  const total = elapsedSeconds.value
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
})

function updateElapsed() {
  if (!props.createdAt) return
  const start = new Date(props.createdAt).getTime()
  const now = Date.now()
  elapsedSeconds.value = Math.max(0, Math.floor((now - start) / 1000))
}

onMounted(() => {
  updateElapsed()
  elapsedInterval = setInterval(updateElapsed, 1000)
})

onUnmounted(() => {
  if (elapsedInterval) clearInterval(elapsedInterval)
})
</script>

<style scoped>
.top-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
  flex: 0 0 auto;
  min-height: 48px;
}
.top-bar-left {
  flex: 1;
  min-width: 0;
}
.session-title {
  font-size: 13px;
  font-weight: 600;
  color: #111;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}
.top-bar-center {
  flex: 0 0 auto;
}
.experiment-chips {
  display: flex;
  align-items: center;
  gap: 6px;
}
.exp-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid #ddd;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  background: #fff;
}
.exp-chip strong {
  font-family: monospace;
  font-size: 10px;
  color: #111;
}
.exp-chip.running strong { color: #2a6edb; }
.exp-chip.success strong { color: #2f7d3b; }
.exp-chip.danger strong { color: #bf3a3a; }
.top-bar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 0 0 auto;
}
.spend-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}
.spend-item .label {
  color: #666;
  text-transform: uppercase;
  font-size: 9px;
}
.spend-item .value {
  font-family: monospace;
  color: #111;
}
.budget-display {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}
.budget-display.warning .llm-calls {
  color: #c76a16;
  font-weight: 600;
}
.llm-calls {
  font-family: monospace;
  color: #111;
}
.budget-bar {
  width: 60px;
  height: 6px;
  background: #e0e0e0;
  border-radius: 3px;
  overflow: hidden;
}
.budget-fill {
  height: 100%;
  background: #2f7d3b;
  transition: width 0.3s ease;
}
.budget-fill.warning {
  background: #c76a16;
}
.dollars {
  font-family: monospace;
  color: #666;
}
.actions {
  display: flex;
  gap: 6px;
  margin-left: 8px;
}
.btn {
  padding: 4px 10px;
  font-size: 11px;
  border: 1px solid #ccc;
  background: #fff;
  cursor: pointer;
}
.btn:hover { background: #f5f5f5; }
.btn.danger { color: #bf3a3a; border-color: #bf3a3a; }
.btn.danger:hover { background: #fff5f5; }
</style>
