import { useRef, useState, useEffect, useCallback } from 'react'

function Slider({ label, value, min, max, step = 1, format, onChange }) {
  const display = format ? format(value) : value
  return (
    <div className="field-group">
      <div className="field-label">
        <span>{label}</span>
        <span className="field-value">{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  )
}

export default function ConfigPanel({
  config,
  field,
  isRunning,
  onConfigChange,
  onLoadField,
  onRun,
  error,
}) {
  const fileRef = useRef(null)
  const [dockMode, setDockMode] = useState(false)
  const [orientDetecting, setOrientDetecting] = useState(false)
  const [orientHint, setOrientHint] = useState(null)  // { angle, conf } after detection

  // Auto-detect: use estimate returned by the upload endpoint
  const handleAutoOrient = useCallback(() => {
    if (!field?.estimated_orientation_deg != null && field.orientation_confidence > 0) {
      const angle = field.estimated_orientation_deg
      onConfigChange({ orientationDeg: angle })
      onLoadField({ ...config, orientationDeg: angle })
      setOrientHint({ angle, conf: field.orientation_confidence })
    }
  }, [field, config, onConfigChange, onLoadField])

  // Auto-load synthetic field on mount
  useEffect(() => {
    if (!field) {
      onLoadField({ ...config, fieldMode: 'synthetic' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleFileChange(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const next = { ...config, fieldMode: 'upload', uploadedFile: file }
    onConfigChange({ fieldMode: 'upload', uploadedFile: file })
    onLoadField(next)
  }

  function handleSyntheticReload() {
    onLoadField({ ...config, fieldMode: 'synthetic' })
  }

  const dockPos = config.dockPositions?.[0] ?? [0, 0]

  return (
    <>
      {/* ── Field ─────────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Field</div>

        <div className="field-group">
          <div className="seg-control" style={{ marginBottom: 8 }}>
            <button
              className={`seg-btn ${config.fieldMode === 'synthetic' ? 'active' : ''}`}
              onClick={() => {
                onConfigChange({ fieldMode: 'synthetic', uploadedFile: null })
                onLoadField({ ...config, fieldMode: 'synthetic', uploadedFile: null })
              }}
            >
              Synthetic
            </button>
            <button
              className={`seg-btn ${config.fieldMode === 'upload' ? 'active' : ''}`}
              onClick={() => fileRef.current?.click()}
            >
              Upload Image
            </button>
          </div>
          <input ref={fileRef} type="file" accept="image/*" onChange={handleFileChange} />
        </div>

        {config.fieldMode === 'upload' && config.uploadedFile && (
          <div style={{ marginBottom: 8 }}>
            <span className="badge badge-success">
              {config.uploadedFile.name}
            </span>
          </div>
        )}

        {config.fieldMode === 'synthetic' && (
          <>
            <Slider
              label="Seed"
              value={config.seed}
              min={0}
              max={99}
              onChange={(v) => {
                onConfigChange({ seed: v })
                onLoadField({ ...config, seed: v })
              }}
            />
            <Slider
              label="Patches"
              value={config.nPatches}
              min={2}
              max={12}
              onChange={(v) => {
                onConfigChange({ nPatches: v })
                onLoadField({ ...config, nPatches: v })
              }}
            />
          </>
        )}

        {field && (
          <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
            {field.nrows}×{field.ncols} grid · {field.n_strips} strips
          </div>
        )}
      </div>

      {/* ── Orientation ───────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Strip Orientation</div>
        <Slider
          label="Crop row angle"
          value={config.orientationDeg}
          min={0}
          max={175}
          step={5}
          format={(v) => `${v}°`}
          onChange={(v) => {
            onConfigChange({ orientationDeg: v })
            onLoadField({ ...config, orientationDeg: v })
          }}
        />

        {/* Auto-detect button — only for uploaded fields with a confident estimate */}
        {field?.estimated_orientation_deg != null && (
          <div style={{ marginTop: 6 }}>
            {field.orientation_confidence >= 0.25 ? (
              <button
                className="btn btn-secondary"
                style={{ fontSize: 11, padding: '4px 10px', width: '100%' }}
                onClick={handleAutoOrient}
                disabled={orientDetecting}
              >
                Auto-detect: {field.estimated_orientation_deg}°
                <span style={{ marginLeft: 6, opacity: 0.6, fontSize: 10 }}>
                  ({Math.round(field.orientation_confidence * 100)}% conf)
                </span>
              </button>
            ) : (
              <div style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.5 }}>
                Auto-detect: no clear row structure detected
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Fleet ─────────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Fleet</div>

        <Slider
          label="Drones"
          value={config.nDrones}
          min={1}
          max={8}
          onChange={(v) => onConfigChange({ nDrones: v })}
        />

        <div className="field-group">
          <div className="field-label">
            <span>Dock position</span>
            <span className="field-value">
              [{dockPos[0]}, {dockPos[1]}]
            </span>
          </div>
          <div className="toggle-row" style={{ marginTop: 4 }}>
            <span className="toggle-label">
              {dockMode ? '🎯 Click grid to place dock' : 'Click grid to place dock'}
            </span>
            <label className="toggle">
              <input
                type="checkbox"
                checked={dockMode}
                onChange={(e) => setDockMode(e.target.checked)}
              />
              <span className="toggle-track" />
            </label>
          </div>
          {dockMode && (
            <div style={{ fontSize: 11, color: 'var(--accent)', marginTop: 4 }}>
              Click any cell in the grid →
            </div>
          )}
        </div>

        {/* Expose dockMode to parent via a hack-free approach: store in config */}
        {/* Parent checks config.dockPlacementMode */}
        {dockMode !== config.dockPlacementMode &&
          onConfigChange({ dockPlacementMode: dockMode })}
      </div>

      {/* ── Simulation ────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Simulation</div>

        <Slider
          label="Battery drain / cell"
          value={config.batteryDrain}
          min={0}
          max={10}
          step={0.5}
          format={(v) => (v === 0 ? 'off' : `${v}%`)}
          onChange={(v) => onConfigChange({ batteryDrain: v })}
        />

        <Slider
          label="Failure probability"
          value={config.failureProb}
          min={0}
          max={0.05}
          step={0.005}
          format={(v) => (v === 0 ? 'off' : `${(v * 100).toFixed(1)}%`)}
          onChange={(v) => onConfigChange({ failureProb: v })}
        />

        {config.batteryDrain > 0 && (
          <Slider
            label="Recharge time"
            value={config.rechargeTime}
            min={0}
            max={50}
            step={5}
            format={(v) => (v === 0 ? 'instant' : `${v} steps`)}
            onChange={(v) => onConfigChange({ rechargeTime: v })}
          />
        )}
      </div>

      {/* ── Run ───────────────────────────────────────────────── */}
      <div style={{ marginTop: 'auto', paddingTop: 8 }}>
        {error && (
          <div style={{
            fontSize: 11,
            color: 'var(--danger)',
            marginBottom: 8,
            padding: '6px 8px',
            background: 'rgba(248,81,73,0.1)',
            borderRadius: 4,
            lineHeight: 1.4,
          }}>
            {error}
          </div>
        )}
        <button
          className="btn btn-primary"
          disabled={!field || isRunning}
          onClick={onRun}
        >
          {isRunning ? '⟳ Planning & simulating…' : '▶ Run Mission'}
        </button>
      </div>
    </>
  )
}
