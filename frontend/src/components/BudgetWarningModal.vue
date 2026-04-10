<template>
  <div v-if="show" class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <h3>Budget at 90%</h3>
      <p class="stats">
        ${{ (budget.dollars_used || 0).toFixed(2) }}<template v-if="budget.dollars_budget"> / ${{ budget.dollars_budget.toFixed(2) }}</template> spent<br>
        {{ (budget.tokens_used || 0).toLocaleString() }} tokens used
      </p>
      <p>The research session has reached 90% of its budget. Would you like to continue?</p>
      <div class="actions">
        <button class="btn" @click="continueSession">Add $10 more budget</button>
        <button class="btn danger" @click="$emit('stop')">Stop Session</button>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  show: { type: Boolean, default: false },
  budget: { type: Object, default: () => ({}) },
  sessionId: { type: String, default: '' },
})

const emit = defineEmits(['close', 'stop', 'continue'])

function continueSession() {
  emit('continue', { additional_budget: 10 })
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal {
  background: #fff;
  padding: 24px;
  max-width: 400px;
  border: 1px solid #ddd;
}
h3 { font-size: 18px; margin-bottom: 12px; }
.stats {
  font-family: monospace;
  font-size: 12px;
  background: #f5f5f5;
  padding: 12px;
  margin-bottom: 12px;
}
.actions { display: flex; gap: 8px; margin-top: 16px; }
.btn { flex: 1; padding: 10px; border: 1px solid #ccc; background: #fff; cursor: pointer; }
.btn:hover { background: #f5f5f5; }
.btn.danger { color: #bf3a3a; border-color: #bf3a3a; }
</style>
