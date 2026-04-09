import { useState, useCallback, useEffect } from 'react'
import ConfigPanel from './components/ConfigPanel.jsx'
import SimView from './components/SimView.jsx'
import MCView from './components/MCView.jsx'
import MCPView from './components/MCPView.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import { uploadField, runSimulation } from './api.js'

// ── Default config ─────────────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  uploadedFile:        null,
  nDrones:             3,
  dockPositions:       [[0, 0]],
  stripMode:           'lawnmower',
  stripWidth:          2,
  orientationDeg:      0,
  batteryLifeMin:      35,
  rechargeTimeMin:     60,
  seedCapacity:        6000,
  seedSpacingM:        1.5,
  windSpeed:           0,
  failureProb:         0,
  survivalRate:        0.4,
  tidalThreshold:      0.6,
  soilNdviThreshold:   0.15,
  waterBlueThreshold:  0.45,
  darkCanopyCutoff:    0.42,
  secondsPerCell:      2.0,
  fieldWidthM:         200,
}

// ── Flow bar ───────────────────────────────────────────────────────────────────

function FlowBar({ fieldReady, baselineReady, mcpReady }) {
  const steps = [
    { n: 1, label: 'Load field',      done: fieldReady },
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

// ── App ────────────────────────────────────────────────────────────────────────

export default function App() {
  const [field,       setField]       = useState(null)
  const [simResult,   setSimResult]   = useState(null)
  const [activeTab,   setActiveTab]   = useState('sim')
  const [showSettings,setShowSettings]= useState(false)
  const [isRunning,   setIsRunning]   = useState(false)
  const [error,       setError]       = useState(null)
  const [apiOk,       setApiOk]       = useState(true)
  const [mcpDone,     setMcpDone]     = useState(false)
  const [config,      setConfig]      = useState(DEFAULT_CONFIG)

  const updateConfig = useCallback((patch) => {
    setConfig(prev => ({ ...prev, ...patch }))
  }, [])

  // ── Field loader (upload pipeline only) ──────────────────────────────────────
  const handleLoadField = useCallback(async (cfg) => {
    if (!cfg.uploadedFile) return
    setError(null)
    try {
      const data = await uploadField(cfg.uploadedFile, {
        target_size:               64,
        orientation_deg:           cfg.orientationDeg,
        seconds_per_cell:          cfg.secondsPerCell,
        soil_ndvi_threshold:       cfg.soilNdviThreshold,
        water_blue_threshold:      cfg.waterBlueThreshold,
        min_brightness_threshold:  cfg.darkCanopyCutoff,
      })
      setField(data)
      setSimResult(null)
    } catch (e) {
      setError(e.message)
      setApiOk(false)
    }
  }, [])

  // ── Auto-load bundled sample on first mount ───────────────────────────────────
  useEffect(() => {
    fetch('/sample/jubail.jpg')
      .then(r => { if (!r.ok) throw new Error('no sample'); return r.blob() })
      .then(blob => {
        const file = new File([blob], 'jubail.jpg', { type: 'image/jpeg' })
        const cfg  = { ...DEFAULT_CONFIG, uploadedFile: file }
        setConfig(cfg)
        handleLoadField(cfg)
      })
      .catch(() => { /* no sample image — user uploads manually */ })
  }, [handleLoadField])

  // ── Run simulation ────────────────────────────────────────────────────────────
  const handleRun = useCallback(async () => {
    if (!field) return
    setIsRunning(true)
    setError(null)
    setSimResult(null)
    try {
      const result = await runSimulation({
        grid:                  field.grid,
        soil_mask:             field.soil_mask ?? null,
        nrows:                 field.nrows,
        ncols:                 field.ncols,
        n_drones:              config.nDrones,
        dock_positions:        config.dockPositions,
        orientation_deg:       config.orientationDeg,
        strip_mode:            config.stripMode,
        strip_width:           config.stripWidth,
        battery_life_minutes:  config.batteryLifeMin,
        recharge_time_minutes: config.rechargeTimeMin,
        seed_capacity:         config.seedCapacity,
        seed_spacing_m:        config.seedSpacingM,
        wind_speed_ms:         config.windSpeed,
        failure_prob:          config.failureProb,
        survival_rate:         config.survivalRate,
        tidal_threshold:       config.tidalThreshold,
        seconds_per_cell:      config.secondsPerCell,
        seed:                  42,
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
      <header className="header">
        <div className={`header-dot ${apiOk ? '' : 'offline'}`} title={apiOk ? 'API connected' : 'API offline'} />
        <span className="header-title">
          Mangrove Reforestation Fleet
          <span className="header-subtitle" style={{ marginLeft: 8 }}>
            Seed Dispersal &amp; Tidal Mission Planning
          </span>
        </span>
        <button className="btn btn-ghost btn-icon" onClick={() => setShowSettings(true)} title="Settings">⚙</button>
      </header>

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

      <main className="main">
        <FlowBar fieldReady={!!field} baselineReady={!!simResult} mcpReady={mcpDone} />

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
          <MCView field={field} config={config} onConfigChange={updateConfig} />
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

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  )
}
