import { useState, useCallback } from 'react'
import ConfigPanel from './components/ConfigPanel.jsx'
import SimView from './components/SimView.jsx'
import MCView from './components/MCView.jsx'
import MCPView from './components/MCPView.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import { getSyntheticField, uploadField, runSimulation } from './api.js'

// ── Utility: build a green-gradient data URL from a priority grid ─────────────

function priorityGridToDataUrl(grid) {
  const nrows = grid.length
  const ncols = grid[0]?.length ?? 0
  const canvas = document.createElement('canvas')
  canvas.width = ncols
  canvas.height = nrows
  const ctx = canvas.getContext('2d')
  const id = ctx.createImageData(ncols, nrows)
  for (let r = 0; r < nrows; r++) {
    for (let c = 0; c < ncols; c++) {
      const v = grid[r][c]
      const i = (r * ncols + c) * 4
      id.data[i]     = Math.round(20 + v * 60)   // R
      id.data[i + 1] = Math.round(55 + v * 160)  // G
      id.data[i + 2] = Math.round(10 + v * 40)   // B
      id.data[i + 3] = 255
    }
  }
  ctx.putImageData(id, 0, 0)
  return canvas.toDataURL()
}

// ── Flow bar ──────────────────────────────────────────────────────────────────

function FlowBar({ fieldReady, baselineReady, mcpReady }) {
  const steps = [
    { n: 1, label: 'Load field',     done: fieldReady },
    { n: 2, label: 'Mission baseline', done: baselineReady },
    { n: 3, label: 'AI coordination', done: mcpReady },
  ]
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 0,
      padding: '5px 16px',
      borderBottom: '1px solid var(--border)',
      background: 'var(--panel)',
    }}>
      {steps.map((s, i) => (
        <div key={s.n} style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '2px 8px',
            fontSize: 11,
            color: s.done ? 'var(--success)' : 'var(--text-dim)',
            fontWeight: s.done ? 600 : 400,
          }}>
            <span style={{
              width: 16, height: 16, borderRadius: '50%',
              background: s.done ? 'var(--success)' : 'var(--border)',
              color: s.done ? '#0d1117' : 'var(--text-dim)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 9, fontWeight: 700, flexShrink: 0,
            }}>
              {s.done ? '✓' : s.n}
            </span>
            {s.label}
          </div>
          {i < steps.length - 1 && (
            <span style={{ color: 'var(--border)', fontSize: 10, padding: '0 4px' }}>→</span>
          )}
        </div>
      ))}
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [field, setField] = useState(null)
  const [simResult, setSimResult] = useState(null)
  const [activeTab, setActiveTab] = useState('sim')
  const [showSettings, setShowSettings] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState(null)
  const [apiOk, setApiOk] = useState(true)
  const [mcpDone, setMcpDone] = useState(false)

  const [config, setConfig] = useState({
    fieldMode: 'synthetic',
    uploadedFile: null,
    seed: 42,
    nPatches: 6,
    nDrones: 3,
    dockPositions: [[0, 0]],
    orientationDeg: 0,
    batteryDrain: 0,
    failureProb: 0,
    secondsPerCell: 2.0,
    rechargeTime: 10,
  })

  const updateConfig = useCallback((patch) => {
    setConfig(prev => ({ ...prev, ...patch }))
  }, [])

  const handleLoadField = useCallback(async (cfg) => {
    setError(null)
    try {
      let data
      if (cfg.fieldMode === 'synthetic') {
        data = await getSyntheticField({
          nrows: 32,
          ncols: 32,
          seed: cfg.seed,
          n_patches: cfg.nPatches,
          seconds_per_cell: cfg.secondsPerCell,
          orientation_deg: cfg.orientationDeg,
        })
        // Generate background image client-side for synthetic fields
        data.image_data_url = priorityGridToDataUrl(data.grid)
        data.estimated_orientation_deg = null
        data.orientation_confidence = 0
      } else if (cfg.uploadedFile) {
        data = await uploadField(cfg.uploadedFile, {
          target_size: 32,
          channel: 'green',
          orientation_deg: cfg.orientationDeg,
          seconds_per_cell: cfg.secondsPerCell,
        })
        // image_data_url, estimated_orientation_deg, orientation_confidence
        // come from the backend for uploaded fields
      } else {
        return
      }
      setField(data)
      setSimResult(null)
    } catch (e) {
      setError(e.message)
      setApiOk(false)
    }
  }, [])

  const handleRun = useCallback(async () => {
    if (!field) return
    setIsRunning(true)
    setError(null)
    setSimResult(null)
    try {
      const result = await runSimulation({
        grid: field.grid,
        nrows: field.nrows,
        ncols: field.ncols,
        n_drones: config.nDrones,
        dock_positions: config.dockPositions,
        orientation_deg: config.orientationDeg,
        battery_drain: config.batteryDrain,
        failure_prob: config.failureProb,
        seconds_per_cell: config.secondsPerCell,
        recharge_time: config.rechargeTime,
        seed: config.seed,
      })
      setSimResult(result)
      setActiveTab('sim')
    } catch (e) {
      setError(e.message)
    } finally {
      setIsRunning(false)
    }
  }, [field, config])

  const tabs = [
    { key: 'sim',  label: 'Mission Baseline' },
    { key: 'mc',   label: 'Fleet Design' },
    { key: 'mcp',  label: '✦ AI Coordination' },
  ]

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className={`header-dot ${apiOk ? '' : 'offline'}`} title={apiOk ? 'API connected' : 'API offline'} />
        <span className="header-title">
          Drone Fleet Optimizer
          <span className="header-subtitle" style={{ marginLeft: 8 }}>
            Failure-Aware Mission Planning
          </span>
        </span>
        <button className="btn btn-ghost btn-icon" onClick={() => setShowSettings(true)} title="Settings">
          ⚙
        </button>
      </header>

      {/* Sidebar */}
      <aside className="sidebar">
        <ConfigPanel
          config={config}
          field={field}
          isRunning={isRunning}
          onConfigChange={updateConfig}
          onLoadField={handleLoadField}
          onRun={handleRun}
          error={error}
        />
      </aside>

      {/* Main */}
      <main className="main">
        {/* Flow bar */}
        <FlowBar
          fieldReady={!!field}
          baselineReady={!!simResult}
          mcpReady={mcpDone}
        />

        {/* Tabs */}
        <div className="tabs">
          {tabs.map((t) => (
            <div
              key={t.key}
              className={`tab ${activeTab === t.key ? 'active' : ''}`}
              onClick={() => setActiveTab(t.key)}
            >
              {t.label}
            </div>
          ))}
        </div>

        {activeTab === 'sim' && (
          <SimView
            field={field}
            simResult={simResult}
            config={config}
            isRunning={isRunning}
            onDockPlace={(pos) => updateConfig({ dockPositions: [pos] })}
          />
        )}

        {activeTab === 'mc' && (
          <MCView
            field={field}
            config={config}
            onConfigChange={updateConfig}
          />
        )}

        {activeTab === 'mcp' && (
          <MCPView
            field={field}
            config={config}
            simResult={simResult}
            onMcpDone={() => setMcpDone(true)}
          />
        )}
      </main>

      {showSettings && (
        <SettingsModal onClose={() => setShowSettings(false)} />
      )}
    </div>
  )
}
