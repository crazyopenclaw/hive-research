<template>
  <div class="graph-panel">
    <div class="graph-header">
      <div class="header-copy">
        <h3>Research Graph</h3>
      </div>
      <div class="graph-controls">
        <!-- Game mode toggle - disabled for now
        <button 
          @click="gameViewMode = !gameViewMode" 
          :class="{ active: gameViewMode }"
          :title="gameViewMode ? 'Switch to graph view' : 'Switch to game view'"
        >
          {{ gameViewMode ? 'Graph' : 'Game' }}
        </button>
        -->
        <button @click="reportOpen = !reportOpen">
          {{ reportOpen ? 'Hide Report' : 'Report' }}
        </button>
        <button @click="$emit('refresh')" :disabled="loading">
          {{ loading ? 'Loading...' : 'Refresh' }}
        </button>
        <button @click="fitView">Fit</button>
      </div>
    </div>

    <div ref="graphRef" class="graph-canvas"></div>

    <div class="agent-overlays">
      <div
        v-for="overlay in overlayItems"
        :key="overlay.id"
        class="agent-overlay"
        :class="{ selected: overlay.id === selectedAgentId }"
        :style="{ left: `${overlay.left}px`, top: `${overlay.top}px` }"
      >
        {{ overlay.text }}
      </div>
    </div>

    <div v-if="reportOpen" class="report-overlay">
      <div class="report-shell">
        <div class="report-shell-header">
          <span>Report</span>
          <button class="close-btn" @click="reportOpen = false">Close</button>
        </div>
        <ReportPanel :sessionId="sessionId" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch, unref } from 'vue'
import { Network } from 'vis-network'
import ReportPanel from './ReportPanel.vue'
import { getSquidIconDataUrl } from '../lib/squidIcons.js'

const props = defineProps({
  sessionId: { type: String, required: true },
  graph: { type: Object, default: () => ({ question: '', nodes: [], edges: [] }) },
  selectedAgentId: { type: String, default: '' },
  events: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['select-agent', 'refresh', 'select-station', 'select-edge'])

const graphRef = ref(null)
const reportOpen = ref(false)
const gameViewMode = ref(false)
const overlayItems = ref([])
const nodePositions = ref({})
const pinnedNodeIds = ref(new Set())
const zoomLevel = ref(1)
const gameViewAgentPositions = ref({})
const gameViewGhosts = ref([])
const graphData = computed(() => props.graph || { question: '', nodes: [], edges: [] })
const normalizedEvents = computed(() => {
  const source = unref(props.events)
  if (Array.isArray(source)) return source
  if (Array.isArray(source?.events)) return source.events
  if (Array.isArray(source?.value)) return source.value
  return []
})

let network = null
let overlayFrame = null
let overlayTrackingFrame = null
let layoutStabilized = false
let dragSelection = []

const EDGE_COLORS = {
  STARTS_FROM: '#d4d4d4',
  SUPPORTS: '#2f7d3b',
  CONTRADICTS: '#bf3a3a',
  EXTENDS: '#2a6edb',
  REFUTES: '#c76a16',
  RUNS_EXPERIMENT: '#e65100',
  POSTS_FINDING: '#2e7d32',
  FORMS_HYPOTHESIS: '#7b1fa2',
  QUESTIONS: '#1565c0',
  CITES: '#5a6a7a',
}

const NODE_STYLES = {
  input: { bg: '#fff8dd', border: '#c6a731', highlightBg: '#fff5c4', highlightBorder: '#a9871a' },
  agent: { bg: '#e3f2fd', border: '#1565c0', highlightBg: '#bbdefb', highlightBorder: '#0d47a1' },
  station: { bg: '#f0f4f8', border: '#5a6a7a', highlightBg: '#e2e8f0', highlightBorder: '#3a4a5a' },
  finding: { bg: '#e8f5e9', border: '#2e7d32', highlightBg: '#c8e6c9', highlightBorder: '#1b5e20' },
  experiment: { bg: '#fff3e0', border: '#e65100', highlightBg: '#ffe0b2', highlightBorder: '#bf360c' },
  hypothesis: { bg: '#f3e5f5', border: '#7b1fa2', highlightBg: '#e1bee7', highlightBorder: '#4a148c' },
  cluster: { bg: '#fafafa', border: '#757575', highlightBg: '#f5f5f5', highlightBorder: '#424242' },
}

const STATION_POSITIONS = {
  lab: { x: 450, y: 300 },
  experiment: { x: 150, y: 300 },
  archive: { x: -150, y: 300 },
  center: { x: -450, y: 300 },
  table: { x: 0, y: -50 },
}

const INPUT_POSITION = { x: 0, y: -350 }

const EVENT_TO_STATION = {
  experiment_queued: 'experiment',
  experiment_started: 'lab',
  experiment_completed: 'lab',
  experiment_failed: 'lab',
  finding_created: 'archive',
  artifact_created: 'archive',
  artifact_updated: 'archive',
  artifact_refuted: 'archive',
  source_ingested: 'center',
  source_discovered: 'center',
  hypothesis_created: 'center',
  assumption_created: 'center',
  note_created: 'center',
  relation_created: 'center',
  debate_started: 'table',
  debate_completed: 'table',
  clusters_computed: 'table',
  intra_cluster_review_started: 'table',
  intra_cluster_review_progress: 'table',
  intra_cluster_review_completed: 'table',
  inter_cluster_debate_started: 'table',
  inter_cluster_debate_progress: 'table',
  inter_cluster_debate_completed: 'table',
  counter_responses_started: 'table',
  counter_response_progress: 'table',
  counter_responses_completed: 'table',
  adjudication_started: 'table',
  adjudication_progress: 'table',
  adjudication_completed: 'table',
  adjudicating_hypothesis: 'table',
}

const ACTIVITY_PRIORITY = {
  table: 10,
  lab: 8,
  experiment: 6,
  archive: 4,
  center: 2,
}

const ACTIVITY_LABELS = {
  experiment_queued: 'Queuing experiment',
  experiment_started: 'Running experiment',
  experiment_completed: 'Experiment done',
  experiment_failed: 'Experiment failed',
  finding_created: 'Posting finding',
  artifact_created: 'Creating artifact',
  artifact_updated: 'Updating artifact',
  artifact_refuted: 'Refuting artifact',
  source_ingested: 'Reading source',
  source_discovered: 'Discovering source',
  hypothesis_created: 'Forming hypothesis',
  assumption_created: 'Stating assumption',
  note_created: 'Writing notes',
  relation_created: 'Linking claims',
  debate_started: 'In debate',
  debate_completed: 'Debate done',
  clusters_computed: 'Computing clusters',
  intra_cluster_review_started: 'Intra-cluster review',
  intra_cluster_review_progress: 'Reviewing cluster',
  intra_cluster_review_completed: 'Review done',
  inter_cluster_debate_started: 'Inter-cluster debate',
  inter_cluster_debate_progress: 'Debating',
  inter_cluster_debate_completed: 'Debate done',
  counter_responses_started: 'Preparing counter',
  counter_response_progress: 'Countering',
  counter_responses_completed: 'Counter done',
  adjudication_started: 'Adjudicating',
  adjudication_progress: 'Judging',
  adjudication_completed: 'Adjudication done',
  adjudicating_hypothesis: 'Judging hypothesis',
}

const PHYSICS_OPTIONS = {
  enabled: true,
  solver: 'forceAtlas2Based',
  stabilization: {
    enabled: true,
    iterations: 80,
    updateInterval: 20,
  },
  forceAtlas2Based: {
    gravitationalConstant: -58,
    centralGravity: 0.015,
    springLength: 180,
    springConstant: 0.03,
    damping: 0.46,
    avoidOverlap: 0.9,
  },
  minVelocity: 0.45,
  timestep: 0.4,
  adaptiveTimestep: true,
}

function getAgentActivities(agentId, events) {
  const now = Date.now()
  const recentWindow = 60000
  const recentEvents = events.filter(e => {
    if (e.agent_id !== agentId) return false
    const eventTime = e.timestamp ? new Date(e.timestamp).getTime() : now
    return (now - eventTime) < recentWindow
  })
  
  const activities = []
  const seenStations = new Set()
  
  for (const event of recentEvents) {
    const eventKind = event.kind || event.event_type
    const station = EVENT_TO_STATION[eventKind]
    if (station && !seenStations.has(station)) {
      seenStations.add(station)
      activities.push({
        station,
        eventKind,
        label: ACTIVITY_LABELS[eventKind] || eventKind,
        priority: ACTIVITY_PRIORITY[station] || 0,
        timestamp: event.timestamp
      })
    }
  }
  
  if (activities.length === 0) {
    activities.push({
      station: 'center',
      eventKind: 'idle',
      label: 'idle',
      priority: 0,
      timestamp: null
    })
  }
  
  return activities.sort((a, b) => b.priority - a.priority)
}

function computeGameViewPositions() {
  if (!gameViewMode.value) return
  
  const agentNodes = graphData.value.nodes.filter(n => n.node_type === 'agent')
  const positions = {}
  const ghosts = []
  const stationAgents = {}
  
  agentNodes.forEach((agent) => {
    const activities = getAgentActivities(agent.id, normalizedEvents.value)
    const primary = activities[0]
    const station = primary.station
    
    if (!stationAgents[station]) {
      stationAgents[station] = []
    }
    stationAgents[station].push({ agent, activities })
  })
  
  Object.entries(stationAgents).forEach(([station, agentsAtStation]) => {
    const basePos = STATION_POSITIONS[station] || STATION_POSITIONS.center
    const count = agentsAtStation.length
    const radius = Math.max(35, count * 15)
    
    agentsAtStation.forEach(({ agent, activities }, index) => {
      const angle = (index / count) * 2 * Math.PI - Math.PI / 2
      const offset = {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius
      }
      
      positions[agent.id] = {
        x: basePos.x + offset.x,
        y: basePos.y + offset.y,
        station: station,
        isPrimary: true
      }
      
      activities.slice(1).forEach((activity, ghostIndex) => {
        const ghostPos = STATION_POSITIONS[activity.station] || STATION_POSITIONS.center
        const ghostAngle = ghostIndex * 1.5
        const ghostOffset = {
          x: Math.cos(ghostAngle) * 25,
          y: Math.sin(ghostAngle) * 25
        }
        ghosts.push({
          id: `${agent.id}-ghost-${ghostIndex}`,
          agentId: agent.id,
          x: ghostPos.x + ghostOffset.x,
          y: ghostPos.y + ghostOffset.y,
          station: activity.station,
          label: activity.label,
          iconKey: agent.icon_key || agent.archetype_name || agent.id
        })
      })
    })
  })
  
  gameViewAgentPositions.value = positions
  gameViewGhosts.value = ghosts
}

function animateAgentToStation(agentId) {
  if (!network) return
  
  const agentPos = gameViewAgentPositions.value[agentId]
  if (!agentPos) return
  
  const currentPos = network.getPositions([agentId])[agentId]
  if (!currentPos) return
  
  const targetX = agentPos.x
  const targetY = agentPos.y
  
  const duration = 500
  const startTime = performance.now()
  const startX = currentPos.x
  const startY = currentPos.y
  
  function animate(currentTime) {
    const elapsed = currentTime - startTime
    const progress = Math.min(elapsed / duration, 1)
    
    const eased = 1 - Math.pow(1 - progress, 3)
    
    const x = startX + (targetX - startX) * eased
    const y = startY + (targetY - startY) * eased
    
    network.moveNode(agentId, x, y)
    
    if (progress < 1) {
      requestAnimationFrame(animate)
    } else {
      scheduleOverlayUpdate()
    }
  }
  
  requestAnimationFrame(animate)
}

function initNetwork() {
  if (!graphRef.value) return

  network = new Network(
    graphRef.value,
    { nodes: [], edges: [] },
    {
      nodes: {
        font: {
          face: 'JetBrains Mono, monospace',
          size: 11,
          color: '#111',
          multi: 'md',
        },
      },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.5 } },
        font: {
          size: 9,
          color: '#444',
          strokeWidth: 3,
          strokeColor: '#fff',
          align: 'middle',
        },
        smooth: { type: 'dynamic' },
      },
      interaction: {
        hover: false,
        dragNodes: true,
        dragView: true,
        zoomView: true,
        selectConnectedEdges: true,
      },
      physics: PHYSICS_OPTIONS,
    }
  )

  network.on('selectNode', (params) => {
    const selectedId = params.nodes[0]
    const node = graphData.value.nodes.find((item) => item.id === selectedId)
    if (!node) return
    
    if (node.node_type === 'agent') {
      emit('select-agent', selectedId)
    } else if (node.node_type === 'station') {
      emit('select-station', node.station_type)
    }
  })
  network.on('selectEdge', (params) => {
    const edgeId = params.edges[0]
    const edge = graphData.value.edges.find((e) => e.id === edgeId)
    if (!edge) return
    
    const sourceId = edge.source
    const node = graphData.value.nodes.find((n) => n.id === sourceId)
    
    if (node && node.node_type === 'agent') {
      emit('select-edge', { agentId: sourceId, edgeType: edge.type, edge })
    }
  })
  network.on('dragStart', (params) => {
    dragSelection = Array.isArray(params.nodes) ? [...params.nodes] : []
    scheduleOverlayUpdate()
  })
  network.on('stabilized', () => {
    layoutStabilized = true
    captureCurrentPositions()
  })
  network.on('afterDrawing', scheduleOverlayUpdate)
  network.on('dragging', () => {
    captureCurrentPositions()
    scheduleOverlayUpdate()
  })
  network.on('zoom', () => {
    if (network) {
      zoomLevel.value = network.getScale()
    }
    scheduleOverlayUpdate()
  })
  network.on('dragEnd', () => {
    if (gameViewMode.value && dragSelection.length) {
      const draggedAgentIds = dragSelection.filter(id => 
        graphData.value.nodes.find(n => n.id === id && n.node_type === 'agent')
      )
      draggedAgentIds.forEach(agentId => {
        animateAgentToStation(agentId)
      })
    } else if (dragSelection.length) {
      const draggedInputNodeIds = graphData.value.nodes
        .filter((node) => node.node_type === 'input' && dragSelection.includes(node.id))
        .map((node) => node.id)
      if (draggedInputNodeIds.length) {
        const nextPinned = new Set(pinnedNodeIds.value)
        draggedInputNodeIds.forEach((id) => nextPinned.add(id))
        pinnedNodeIds.value = nextPinned
      }
    }
    dragSelection = []
    captureCurrentPositions()
    scheduleOverlayUpdate()
    network?.startSimulation()
  })
}

function updateGraph() {
  if (!network) return

  if (gameViewMode.value) {
    computeGameViewPositions()
  }

  const nodeIds = graphData.value.nodes.map((node) => node.id)
  const previousPositions = {
    ...nodePositions.value,
    ...network.getPositions(nodeIds),
  }
  const preserveLayout = nodeIds.length > 0 && nodeIds.every((id) => previousPositions[id])
  network.setOptions({
    physics: {
      ...PHYSICS_OPTIONS,
      enabled: !gameViewMode.value,
      stabilization: {
        ...PHYSICS_OPTIONS.stabilization,
        enabled: !layoutStabilized && !preserveLayout && !gameViewMode.value,
      },
    },
  })

  let visNodes = []
  let visEdges = []

  if (gameViewMode.value) {
    const stationNodes = graphData.value.nodes
      .filter(node => node.node_type === 'station')
      .map((node) => {
        const stationType = node.station_type || 'center'
        const position = STATION_POSITIONS[stationType] || STATION_POSITIONS.center
        const style = NODE_STYLES.station
        return {
          id: node.id,
          label: node.label || 'Station',
          shape: 'box',
          color: {
            background: style.bg,
            border: style.border,
          },
          borderWidth: 2,
          margin: 12,
          widthConstraint: { maximum: 120 },
          font: { face: 'JetBrains Mono, monospace', size: 10, color: '#444', align: 'center' },
          physics: false,
          fixed: { x: true, y: true },
          x: position.x,
          y: position.y,
        }
      })

    const agentNodes = graphData.value.nodes
      .filter(node => node.node_type === 'agent')
      .map((node) => {
        const isSelected = props.selectedAgentId === node.id
        const gamePos = gameViewAgentPositions.value[node.id] || { x: 0, y: 0 }
        return {
          id: node.id,
          label: '',
          shape: 'image',
          image: getSquidIconDataUrl(node.icon_key || node.archetype_name || node.id),
          size: isSelected ? 48 : 40,
          shadow: isSelected ? { enabled: true, color: 'rgba(17, 17, 17, 0.24)', size: 18, x: 0, y: 0 } : false,
          physics: false,
          x: gamePos.x,
          y: gamePos.y,
        }
      })

    const ghostNodes = gameViewGhosts.value.map((ghost) => ({
      id: ghost.id,
      label: ghost.label,
      shape: 'image',
      image: getSquidIconDataUrl(ghost.iconKey),
      size: 24,
      opacity: 0.5,
      physics: false,
      fixed: { x: true, y: true },
      x: ghost.x,
      y: ghost.y,
      font: { face: 'JetBrains Mono, monospace', size: 8, color: '#666', align: 'center' },
    }))

    const ghostEdges = gameViewGhosts.value.map((ghost) => ({
      id: `ghost-edge-${ghost.id}`,
      from: ghost.agentId,
      to: ghost.id,
      color: { color: 'rgba(100, 100, 100, 0.3)', opacity: 0.3 },
      width: 1,
      dashes: [3, 3],
      arrows: { to: { enabled: false } },
      smooth: { type: 'continuous' },
    }))

    visNodes = [...stationNodes, ...agentNodes, ...ghostNodes]
    visEdges = ghostEdges
  } else {
    visNodes = graphData.value.nodes.map((node) => {
      const prior = previousPositions[node.id]
      const isSelected = props.selectedAgentId === node.id
      const nodeType = node.node_type || 'agent'

      if (nodeType === 'input') {
        return {
          id: node.id,
          label: node.label || 'Research question',
          shape: 'box',
          color: {
            background: NODE_STYLES.input.bg,
            border: NODE_STYLES.input.border,
            highlight: { background: NODE_STYLES.input.highlightBg, border: NODE_STYLES.input.highlightBorder },
          },
          borderWidth: 2,
          margin: 14,
          widthConstraint: { maximum: 260 },
          font: {
            face: 'JetBrains Mono, monospace',
            size: 11,
            color: '#111',
            multi: 'md',
          },
          mass: 0.75,
          physics: false,
          fixed: { x: true, y: true },
          x: INPUT_POSITION.x,
          y: INPUT_POSITION.y,
        }
      }

      if (nodeType === 'station') {
        const stationType = node.station_type || 'center'
        const position = STATION_POSITIONS[stationType] || STATION_POSITIONS.center
        const style = NODE_STYLES.station
        return {
          id: node.id,
          label: node.label || 'Station',
          shape: 'box',
          color: {
            background: style.bg,
            border: style.border,
            highlight: { background: style.highlightBg, border: style.highlightBorder },
          },
          borderWidth: 2,
          margin: 12,
          widthConstraint: { maximum: 120 },
          font: { face: 'JetBrains Mono, monospace', size: 10, color: '#444', align: 'center' },
          physics: false,
          fixed: { x: true, y: true },
          x: position.x,
          y: position.y,
        }
      }

      if (nodeType === 'agent') {
        return {
          id: node.id,
          label: '',
          shape: 'image',
          image: getSquidIconDataUrl(node.icon_key || node.archetype_name || node.id),
          size: isSelected ? 48 : 40,
          shadow: isSelected ? { enabled: true, color: 'rgba(17, 17, 17, 0.24)', size: 18, x: 0, y: 0 } : false,
          ...(prior ? { x: prior.x, y: prior.y } : {}),
        }
      }

      if (nodeType === 'finding') {
        const showLabel = zoomLevel.value >= 0.6
        const style = NODE_STYLES.finding
        return {
          id: node.id,
          label: showLabel ? truncateLabel(node.label || node.statement || 'Finding', 35) : '',
          shape: 'diamond',
          color: {
            background: style.bg,
            border: style.border,
            highlight: { background: style.highlightBg, border: style.highlightBorder },
          },
          borderWidth: isSelected ? 3 : 2,
          size: isSelected ? 28 : 22,
          font: { face: 'JetBrains Mono, monospace', size: 9, color: '#111', align: 'center' },
          margin: 8,
          ...(prior ? { x: prior.x, y: prior.y } : {}),
        }
      }

      if (nodeType === 'experiment') {
        const showLabel = zoomLevel.value >= 0.5
        const style = NODE_STYLES.experiment
        const statusShape = node.status === 'completed' ? 'hexagon' : node.status === 'failed' ? 'triangle' : 'square'
        return {
          id: node.id,
          label: showLabel ? truncateLabel(node.label || node.name || 'Experiment', 30) : '',
          shape: statusShape,
          color: {
            background: style.bg,
            border: style.border,
            highlight: { background: style.highlightBg, border: style.highlightBorder },
          },
          borderWidth: isSelected ? 3 : 2,
          size: isSelected ? 26 : 20,
          font: { face: 'JetBrains Mono, monospace', size: 9, color: '#111', align: 'center' },
          margin: 8,
          ...(prior ? { x: prior.x, y: prior.y } : {}),
        }
      }

      if (nodeType === 'hypothesis') {
        const showLabel = zoomLevel.value >= 0.7
        const style = NODE_STYLES.hypothesis
        return {
          id: node.id,
          label: showLabel ? truncateLabel(node.label || node.statement || 'Hypothesis', 40) : '',
          shape: 'ellipse',
          color: {
            background: style.bg,
            border: style.border,
            highlight: { background: style.highlightBg, border: style.highlightBorder },
          },
          borderWidth: isSelected ? 3 : 2,
          size: isSelected ? 24 : 18,
          font: { face: 'JetBrains Mono, monospace', size: 9, color: '#111', align: 'center' },
          margin: 10,
          ...(prior ? { x: prior.x, y: prior.y } : {}),
        }
      }

      if (nodeType === 'cluster') {
        const style = NODE_STYLES.cluster
        return {
          id: node.id,
          label: node.label || node.name || 'Cluster',
          shape: 'circle',
          color: {
            background: style.bg,
            border: style.border,
            highlight: { background: style.highlightBg, border: style.highlightBorder },
          },
          borderWidth: 2,
          size: node.member_count ? Math.min(60, 30 + node.member_count * 2) : 40,
          font: { face: 'JetBrains Mono, monospace', size: 10, color: '#111', align: 'center' },
          margin: 12,
          ...(prior ? { x: prior.x, y: prior.y } : {}),
        }
      }

      return {
        id: node.id,
        label: node.label || '',
        shape: 'dot',
        size: 10,
        color: { background: '#eee', border: '#999' },
        ...(prior ? { x: prior.x, y: prior.y } : {}),
      }
    })

    visEdges = graphData.value.edges.map((edge) => {
      const color = EDGE_COLORS[edge.type] || '#888'
      if (edge.type === 'STARTS_FROM') {
        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          color: { color, highlight: color, opacity: 0.7 },
          width: 0.8,
          length: 140,
          arrows: { to: { enabled: false } },
          smooth: { type: 'cubicBezier', roundness: 0.15 },
        }
      }

      if (['RUNS_EXPERIMENT', 'POSTS_FINDING', 'FORMS_HYPOTHESIS'].includes(edge.type)) {
        const labelText = edge.summary 
          ? `${edge.type.replace('_', ' ')}: ${edge.summary.slice(0, 40)}`
          : `${edge.type.replace('_', ' ')} (${edge.count || 1})`
        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          label: labelText,
          color: { color, highlight: color },
          width: 1.5 + (edge.count || 1) * 0.3,
          arrows: { to: { enabled: true, scaleFactor: 0.4 } },
          smooth: { type: 'curvedCW', roundness: 0.2 },
        }
      }

      if (edge.type === 'QUESTIONS') {
        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          label: edge.count > 1 ? `questions (${edge.count})` : 'questions',
          color: { color, highlight: color },
          width: 1.2,
          dashes: [5, 5],
          arrows: { to: { enabled: true, scaleFactor: 0.5 } },
        }
      }

      if (edge.type === 'CONTRADICTS' || edge.type === 'REFUTES') {
        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          label: edge.count > 1 ? `${edge.type} (${edge.count})` : edge.type,
          color: { color, highlight: color },
          width: Math.min(5, 1.5 + (edge.count || 1)),
          dashes: [8, 4],
          arrows: { to: { enabled: true, scaleFactor: 0.5 } },
        }
      }

      if (edge.type === 'CITES') {
        return {
          id: edge.id,
          from: edge.source,
          to: edge.target,
          label: edge.count > 1 ? `cites (${edge.count})` : 'cites',
          color: { color, highlight: color, opacity: 0.6 },
          width: 1,
          arrows: { to: { enabled: true, scaleFactor: 0.4 } },
        }
      }

      return {
        id: edge.id,
        from: edge.source,
        to: edge.target,
        label: edge.count > 1 ? `${edge.type} (${edge.count})` : edge.type,
        color: { color, highlight: color },
        width: Math.min(5, 1.5 + (edge.count || 1)),
      }
    })
  }

  network.setData({ nodes: visNodes, edges: visEdges })
  captureCurrentPositions()
  
  if (gameViewMode.value && Object.keys(gameViewAgentPositions.value).length > 0) {
    const agentIds = Object.keys(gameViewAgentPositions.value)
    agentIds.forEach(agentId => {
      const pos = gameViewAgentPositions.value[agentId]
      if (pos && network) {
        network.moveNode(agentId, pos.x, pos.y)
      }
    })
  }
  
  if (!gameViewMode.value) {
    network.startSimulation()
  }
  syncExternalSelection()
  scheduleOverlayUpdate()
}

function syncExternalSelection() {
  if (!network || !props.selectedAgentId) return
  const node = graphData.value.nodes.find((item) => item.id === props.selectedAgentId)
  if (!node || node.node_type !== 'agent') return
  network.selectNodes([props.selectedAgentId])
}

function fitView() {
  if (network) {
    network.fit({ animation: true })
  }
}

function captureCurrentPositions() {
  if (!network) return
  const nodeIds = graphData.value.nodes.map((node) => node.id)
  if (!nodeIds.length) return
  nodePositions.value = {
    ...nodePositions.value,
    ...network.getPositions(nodeIds),
  }
}

function scheduleOverlayUpdate() {
  if (overlayFrame) {
    cancelAnimationFrame(overlayFrame)
  }
  overlayFrame = requestAnimationFrame(() => {
    overlayFrame = null
    updateOverlayPositions()
  })
}

function startOverlayTracking() {
  stopOverlayTracking()
  const tick = () => {
    overlayTrackingFrame = requestAnimationFrame(tick)
    updateOverlayPositions()
  }
  overlayTrackingFrame = requestAnimationFrame(tick)
}

function stopOverlayTracking() {
  if (overlayTrackingFrame) {
    cancelAnimationFrame(overlayTrackingFrame)
    overlayTrackingFrame = null
  }
}

function updateOverlayPositions() {
  if (!network) return
  const agentNodes = graphData.value.nodes.filter((node) => node.node_type === 'agent')
  if (!agentNodes.length) {
    overlayItems.value = []
    return
  }
  const positions = network.getPositions(agentNodes.map((node) => node.id))
  overlayItems.value = agentNodes.map((node) => {
    const canvasPoint = positions[node.id]
    if (!canvasPoint) {
      return null
    }
    const domPoint = network.canvasToDOM(canvasPoint)
    return {
      id: node.id,
      left: domPoint.x,
      top: domPoint.y - 38,
      text: summarizeAgentActivity(node),
    }
  }).filter(Boolean)
}

function summarizeAgentActivity(node) {
  const latestEvent = normalizedEvents.value.find((event) => {
    return event?.agent_id === node.id
  })
  if (!latestEvent) {
    return node.current_status_text || (node.status === 'sleeping' ? 'sleeping' : 'researching')
  }

  if (latestEvent.status_text) {
    return shortText(latestEvent.status_text, 28)
  }

  const kind = latestEvent.kind
  const payload = latestEvent.payload || {}
  if (kind === 'search_completed') return `searching: ${shortText(payload.query || latestEvent.summary, 28)}`
  if (kind === 'source_ingested') return 'reading source'
  if (kind === 'note_created') return 'writing notes'
  if (kind === 'assumption_created') return 'stating assumptions'
  if (kind === 'hypothesis_created') return 'forming hypothesis'
  if (kind === 'finding_created') return 'posting finding'
  if (kind === 'relation_created') return 'linking claims'
  if (kind === 'experiment_queued') return 'queueing experiment'
  if (kind === 'experiment_started') return 'running sandbox'
  if (kind === 'experiment_completed') return 'sandbox complete'
  if (kind === 'experiment_failed') return 'sandbox failed'
  if (kind === 'reviewed_hypothesis') return `review: ${shortText(payload.verdict || 'done', 20)}`
  if (kind === 'paused') return 'paused'
  if (kind === 'error') return 'error'
  return node.current_status_text || latestEvent.title || 'working'
}

function shortText(value, maxLength) {
  const text = String(value || '').trim()
  if (!text) return 'working'
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text
}

function truncateLabel(value, maxLength) {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text
}

onMounted(async () => {
  await nextTick()
  initNetwork()
  startOverlayTracking()
  updateGraph()
})

onUnmounted(() => {
  if (overlayFrame) {
    cancelAnimationFrame(overlayFrame)
  }
  stopOverlayTracking()
  if (network) {
    network.destroy()
  }
})

watch(
  () => props.sessionId,
  async () => {
    reportOpen.value = false
    nodePositions.value = {}
    pinnedNodeIds.value = new Set()
    layoutStabilized = false
    await nextTick()
    updateGraph()
  }
)

watch(
  () => props.graph,
  () => {
    updateGraph()
  },
  { deep: true }
)

watch(() => props.selectedAgentId, () => {
  syncExternalSelection()
  scheduleOverlayUpdate()
})

watch(
  () => normalizedEvents.value,
  () => {
    if (gameViewMode.value) {
      computeGameViewPositions()
      updateGraph()
    }
    scheduleOverlayUpdate()
  },
  { deep: true }
)

watch(gameViewMode, (enabled) => {
  if (enabled) {
    computeGameViewPositions()
  }
  updateGraph()
})
</script>

<style scoped>
.graph-panel {
  position: relative;
  height: 100%;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.graph-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid #eee;
  gap: 12px;
  background: rgba(255, 255, 255, 0.96);
}
.header-copy {
  min-width: 0;
}
.graph-header h3 {
  margin: 0 0 4px;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 1px;
}
.question {
  margin: 0;
  font-size: 11px;
  line-height: 1.35;
  color: #666;
  max-width: 520px;
}
.graph-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.graph-controls button,
.close-btn {
  padding: 5px 10px;
  border: 1px solid #111;
  background: #fff;
  font-family: inherit;
  font-size: 10px;
  text-transform: uppercase;
  cursor: pointer;
}
.graph-controls button:hover,
.close-btn:hover {
  background: #f5f5f5;
}
.graph-controls button.active {
  background: #111;
  color: #fff;
}
.graph-canvas {
  flex: 1;
  min-height: 400px;
  background: #fff;
}
.agent-overlays {
  position: absolute;
  inset: 48px 0 0 0;
  pointer-events: none;
}
.agent-overlay {
  position: absolute;
  transform: translate(-50%, -100%);
  max-width: 160px;
  padding: 3px 8px;
  border: 1px solid #ddd;
  background: rgba(255, 255, 255, 0.96);
  font-size: 10px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.08);
}
.agent-overlay.selected {
  border-color: #111;
  color: #111;
}
.report-overlay {
  position: absolute;
  top: 72px;
  right: 16px;
  bottom: 16px;
  width: min(460px, calc(100% - 32px));
  pointer-events: none;
}
.report-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid #ddd;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.14);
  pointer-events: auto;
}
.report-shell-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #eee;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
}
.report-shell :deep(.report-panel) {
  margin: 0;
  padding: 12px;
  min-height: 0;
  overflow: auto;
}
</style>
