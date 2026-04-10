/**
 * Session store for the backend-native API.
 * Holds overview, graph, agent list, live events, and the selected session.
 */

import { ref } from 'vue'
import { api } from '../api/index.js'
import {
  createDefaultAgentLabels,
  lookupAgentLabel,
  rememberAgentLabel,
} from '../lib/eventView.js'

const SOURCE_PROGRESS_KINDS = new Set([
  'downloading_source',
  'download_source_progress',
  'ingesting_source',
  'ingested_search_source',
  'search_source_already_ingested',
])

export function useSessionStore() {
  const session = ref(null)
  const overview = ref(null)
  const graphView = ref({ question: '', nodes: [], edges: [] })
  const agents = ref([])
  const events = ref([])
  const loading = ref(false)
  const error = ref(null)
  const syncVersion = ref(0)
  const currentSessionId = ref('')
  const agentLabels = ref(createDefaultAgentLabels())
  const budget = ref({
    tokens_used: 0,
    dollars_used: 0,
    dollars_budget: 0,
    percentage: 0,
    is_warning: false,
  })

  const centerMode = ref('graph')
  const inspectorTab = ref('overview')
  const selectedNodeType = ref(null)
  const selectedNodeId = ref(null)
  const bottomDockExpanded = ref(false)

  let eventSource = null
  let refreshTimer = null
  let pollTimer = null

  async function load(sessionId) {
    loading.value = true
    error.value = null
    currentSessionId.value = sessionId
    try {
      await Promise.all([
        loadSession(sessionId),
        loadOverview(sessionId),
        loadGraph(sessionId),
        loadAgents(sessionId),
        loadRecentEvents(sessionId),
      ])
      events.value = rebuildEventFeed(events.value.map((event) => normalizeEvent(event)).filter(Boolean))
      syncVersion.value++
      connectSSE(sessionId)
      startPolling()
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }

  async function loadSession(sessionId) {
    session.value = await api.getSession(sessionId)
  }

  async function loadOverview(sessionId) {
    overview.value = await api.getOverview(sessionId)
    if (overview.value?.budget) {
      budget.value = {
        tokens_used: overview.value.budget.tokens_used || 0,
        dollars_used: overview.value.budget.dollars_used || overview.value.budget.spent || 0,
        dollars_budget: overview.value.budget.dollars_budget || 0,
        percentage: overview.value.budget.percentage || 0,
        is_warning: overview.value.budget.is_warning || false,
      }
    }
    if (session.value && overview.value) {
      session.value = {
        ...session.value,
        status: overview.value.status ?? session.value.status,
      }
    }
  }

  async function loadGraph(sessionId) {
    graphView.value = await api.getGraph(sessionId)
  }

  async function loadAgents(sessionId) {
    const result = await api.getAgents(sessionId)
    agents.value = result.agents || []
    for (const agent of agents.value) {
      rememberAgentLabel(agentLabels.value, agent.id, agent.name || agent.label || agent.id)
    }
    events.value = rebuildEventFeed(events.value.map((event) => normalizeEvent(event)).filter(Boolean))
  }

  async function loadRecentEvents(sessionId) {
    const result = await api.getEvents(sessionId, 80)
    events.value = rebuildEventFeed((result.events || []).map(normalizeEvent).filter(Boolean))
  }

  function connectSSE(sessionId) {
    if (eventSource) eventSource.close()
    eventSource = new EventSource(api.streamUrl(sessionId))
    eventSource.onmessage = (e) => {
      try {
        const event = normalizeEvent(JSON.parse(e.data))
        if (!event || event.kind === 'keepalive') return
        events.value = appendEvent(events.value, event)
        queueRefresh()
      } catch {
        // Ignore malformed event payloads.
      }
    }
    eventSource.onerror = () => {
      setTimeout(() => {
        if (currentSessionId.value === sessionId) {
          connectSSE(sessionId)
        }
      }, 5000)
    }
  }

  function queueRefresh() {
    if (!currentSessionId.value || refreshTimer) return
    refreshTimer = setTimeout(async () => {
      refreshTimer = null
      await refreshSessionState()
    }, 350)
  }

  async function refreshSessionState() {
    if (!currentSessionId.value) return
    await Promise.all([
      loadSession(currentSessionId.value),
      loadOverview(currentSessionId.value),
      loadGraph(currentSessionId.value),
      loadAgents(currentSessionId.value),
    ])
    syncVersion.value++
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = setInterval(async () => {
      const status = overview.value?.status || session.value?.status
      if (!currentSessionId.value) return
      if (!['starting', 'active', 'interrupted'].includes(status)) return
      await refreshSessionState()
    }, 5000)
  }

  function disconnect() {
    if (eventSource) eventSource.close()
    if (refreshTimer) clearTimeout(refreshTimer)
    if (pollTimer) clearInterval(pollTimer)
    eventSource = null
    refreshTimer = null
    pollTimer = null
  }

  function normalizeEvent(event) {
    const payload = event && typeof event === 'object' ? event : {}
    const eventPayload =
      payload.payload && typeof payload.payload === 'object' ? payload.payload : {}

    const kind = payload.kind || payload.type || 'event'
    if (kind === 'state_snapshot' || kind === 'STATE_SNAPSHOT') {
      if (payload.dollars_used !== undefined || payload.llm_budget_usd !== undefined) {
        budget.value = {
          tokens_used: payload.tokens_used || 0,
          dollars_used: payload.dollars_used || 0,
          dollars_budget: payload.llm_budget_usd || payload.budget_total || budget.value.dollars_budget,
          percentage: Math.round(((payload.dollars_used || 0) / Math.max(0.01, payload.llm_budget_usd || payload.budget_total || budget.value.dollars_budget || 1)) * 100),
          is_warning: payload.budget_warning || false,
        }
      }
    }
    if (kind === 'budget_warning' || kind === 'BUDGET_WARNING') {
      budget.value = {
        ...budget.value,
        tokens_used: eventPayload.tokens_used || budget.value.tokens_used,
        dollars_used: eventPayload.dollars_used || budget.value.dollars_used,
        dollars_budget: eventPayload.llm_budget_usd || eventPayload.budget_total || budget.value.dollars_budget,
        percentage: eventPayload.percentage || budget.value.percentage,
        is_warning: true,
      }
    }

    if (isHiddenEvent(payload, eventPayload)) {
      return null
    }
    if (payload.agent_id && eventPayload.name) {
      rememberAgentLabel(agentLabels.value, payload.agent_id, eventPayload.name)
    }
    if (payload.kind === 'agent_spawned' && payload.agent_id && payload.title) {
      rememberAgentLabel(agentLabels.value, payload.agent_id, payload.title)
    }
    const displayAgent = lookupAgentLabel(agentLabels.value, payload.agent_id || '')
    const displayToAgent = lookupAgentLabel(agentLabels.value, eventPayload.to_agent || '')
    return {
      timestamp: payload.timestamp || new Date().toISOString(),
      session_id: payload.session_id || currentSessionId.value,
      agent_id: payload.agent_id || null,
      display_agent: displayAgent || null,
      display_to_agent: displayToAgent || null,
      kind: kind,
      title: payload.title || 'Event',
      summary: payload.summary || '',
      payload: eventPayload,
      refs: payload.refs && typeof payload.refs === 'object' ? payload.refs : {},
      status_text: payload.status_text || '',
    }
  }

  function appendEvent(currentEvents, event) {
    const next = [...currentEvents]
    const groupKey = eventGroupKey(event)
    if (!groupKey) {
      next.unshift(event)
      return next.slice(0, 250)
    }

    const index = next.findIndex((item) => eventGroupKey(item) === groupKey)
    if (index === -1) {
      next.unshift({ ...event, _groupKey: groupKey })
      return next.slice(0, 250)
    }

    const merged = {
      ...next[index],
      ...event,
      _groupKey: groupKey,
    }
    next.splice(index, 1)
    next.unshift(merged)
    return next.slice(0, 250)
  }

  function rebuildEventFeed(sourceEvents) {
    const merged = []
    for (const event of sourceEvents) {
      const groupKey = eventGroupKey(event)
      if (!groupKey) {
        merged.push(event)
        continue
      }
      const index = merged.findIndex((item) => eventGroupKey(item) === groupKey)
      if (index === -1) {
        merged.push({ ...event, _groupKey: groupKey })
      } else {
        merged[index] = {
          ...merged[index],
          ...event,
          _groupKey: groupKey,
        }
      }
    }
    return merged.reverse().slice(0, 250)
  }

  function eventGroupKey(event) {
    const kind = String(event?.kind || '')
    if (!SOURCE_PROGRESS_KINDS.has(kind)) return ''
    const payload = event?.payload || {}
    const source = String(payload.source || 'source')
    const identifier = String(
      payload.arxiv_id ||
      payload.source_id ||
      payload.title ||
      event?.refs?.artifact_id ||
      ''
    )
    const agent = String(event?.agent_id || 'system')
    return identifier ? `source:${agent}:${source}:${identifier}` : ''
  }

  function isHiddenEvent(event, eventPayload) {
    const kind = event.kind || event.type || 'event'
    if (kind !== 'artifact_updated') return false
    const updatedFields = Array.isArray(eventPayload.updated_fields)
      ? eventPayload.updated_fields
      : []
    const properties = eventPayload.properties && typeof eventPayload.properties === 'object'
      ? eventPayload.properties
      : {}
    const label = String(eventPayload.label || '')
    if (updatedFields.length === 1 && ['embedding_id', 'read'].includes(updatedFields[0])) {
      return true
    }
    if (label === 'Experiment' && 'status' in properties) {
      return true
    }
    return false
  }

  return {
    session,
    overview,
    graphView,
    agents,
    events,
    loading,
    error,
    syncVersion,
    budget,
    load,
    disconnect,
    refreshSessionState,
    centerMode,
    inspectorTab,
    selectedNodeType,
    selectedNodeId,
    bottomDockExpanded,
  }
}
