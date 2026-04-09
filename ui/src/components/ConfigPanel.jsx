import { useRef, useState } from 'react'

// ── Tooltip ────────────────────────────────────────────────────────────────────

function Tip({ children }) {
  const [show, setShow] = useState(false)
  return (
    <span style={{ position: 'relative', display: 'inline-flex', marginLeft: 3 }}>
      <span
        style={{
          fontSize: 8, color: 'var(--text-dim)', cursor: 'help',
          width: 12, height: 12, borderRadius: '50%',
          border: '1px solid currentColor',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0, lineHeight: 1,
        }}
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
      >
        i
      </span>
      {show && (
        <div style={{
          position: 'absolute', bottom: '100%', left: '50%',
          transform: 'translateX(-50%)',
          background: '#1c2128', border: '1px solid var(--border)',
          borderRadius: 6, padding: '7px 9px',
          fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.55,
          width: 215, zIndex: 200, marginBottom: 5,
          pointerEvents: 'none', whiteSpace: 'normal',
          boxShadow: '0 4px 14px rgba(0,0,0,0.5)',
        }}>
          {children}
        </div>
      )}
    </span>
  )
}

// ── Slider ─────────────────────────────────────────────────────────────────────

function Slider({ label, value, min, max, step = 1, format, onChange, tip }) {
  const display = format ? format(value) : value
  return (
    <div className="field-group">
      <div className="field-label">
        <span style={{ display: 'flex', alignItems: 'center' }}>
          {label}
          {tip && <Tip>{tip}</Tip>}
        </span>
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

// ── NumberInput ────────────────────────────────────────────────────────────────

function NumberInput({ label, value, min, max, step = 1, unit, onChange, tip }) {
  return (
    <div className="field-group">
      <div className="field-label">
        <span style={{ display: 'flex', alignItems: 'center' }}>
          {label}
          {tip && <Tip>{tip}</Tip>}
        </span>
        {unit && <span className="field-value">{unit}</span>}
      </div>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value)
          if (!isNaN(n)) onChange(n)
        }}
        style={{
          width: '100%',
          background: 'var(--input-bg, #0d1117)',
          border: '1px solid var(--border)',
          color: 'var(--text)',
          padding: '4px 7px',
          borderRadius: 4,
          fontSize: 11,
          boxSizing: 'border-box',
        }}
      />
    </div>
  )
}

// ── ConfigPanel ────────────────────────────────────────────────────────────────

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

  function handleFileChange(e) {
    const file = e.target.files?.[0]
    if (!file) return
    onConfigChange({ uploadedFile: file })
    onLoadField({ ...config, uploadedFile: file })
  }

  const dockPos = config.dockPositions?.[0] ?? [0, 0]

  const plantableCells = field?.plantable_cells
  const infoLabel = plantableCells != null
    ? `${plantableCells} plantable cells · ${field?.n_strips ?? '?'} strips`
    : field
      ? `${field.nrows}×${field.ncols} grid · ${field.n_strips ?? '?'} strips`
      : null

  // Derived m/cell for display
  const mPerCell = field ? (config.fieldWidthM / (field.ncols ?? 32)).toFixed(1) : null

  return (
    <>
      {/* ── Field ──────────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Field</div>

        <div className="field-group">
          <button
            className="btn btn-secondary"
            style={{ width: '100%' }}
            onClick={() => fileRef.current?.click()}
          >
            {config.uploadedFile ? '↑ Change image' : '↑ Upload aerial image'}
          </button>
          <input ref={fileRef} type="file" accept="image/*" onChange={handleFileChange} style={{ display: 'none' }} />
        </div>

        {config.uploadedFile && (
          <div style={{ marginBottom: 6 }}>
            <span className="badge badge-success">{config.uploadedFile.name}</span>
          </div>
        )}

        <Slider
          label="NDVI cutoff"
          value={config.soilNdviThreshold}
          min={0.10}
          max={0.25}
          step={0.01}
          format={(v) => v.toFixed(2)}
          onChange={(v) => {
            onConfigChange({ soilNdviThreshold: v })
            if (config.uploadedFile) onLoadField({ ...config, soilNdviThreshold: v })
          }}
          tip="Cells with pseudo-NDVI (green−red) above this value are classified as existing canopy or teal water and excluded. Raise to include more vegetated fringe cells."
        />
        <Slider
          label="Water (blue) cutoff"
          value={config.waterBlueThreshold}
          min={0.40}
          max={0.55}
          step={0.01}
          format={(v) => v.toFixed(2)}
          onChange={(v) => {
            onConfigChange({ waterBlueThreshold: v })
            if (config.uploadedFile) onLoadField({ ...config, waterBlueThreshold: v })
          }}
          tip="Cells with a high normalised blue channel are classified as open water and excluded. Lower this if shallow tidal channels are being mis-classified as land."
        />
        <Slider
          label="Canopy brightness cutoff"
          value={config.darkCanopyCutoff}
          min={0.30}
          max={0.55}
          step={0.01}
          format={(v) => v.toFixed(2)}
          onChange={(v) => {
            onConfigChange({ darkCanopyCutoff: v })
            if (config.uploadedFile) onLoadField({ ...config, darkCanopyCutoff: v })
          }}
          tip="Cells darker than this brightness level are treated as established mangrove canopy and excluded — no replanting needed. Raise if dense canopy patches are being incorrectly included."
        />

        <NumberInput
          label="Field width"
          value={config.fieldWidthM}
          min={10}
          max={10000}
          step={10}
          unit="m"
          onChange={(v) => onConfigChange({ fieldWidthM: v })}
          tip="Physical width of the field in metres. Set this at upload time to match your aerial image extent. Used to draw the scale bar on the map and to calculate real-world seed spacing."
        />
        {mPerCell && (
          <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: -2, marginBottom: 4 }}>
            ≈ {mPerCell} m / cell
          </div>
        )}

        {infoLabel && (
          <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
            {infoLabel}
          </div>
        )}
      </div>

      {/* ── Strip Mode ─────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Strip Mode</div>

        <div className="field-group">
          <div className="seg-control" style={{ marginBottom: 8 }}>
            <button
              className={`seg-btn ${config.stripMode === 'lawnmower' ? 'active' : ''}`}
              onClick={() => {
                onConfigChange({ stripMode: 'lawnmower' })
                onLoadField({ ...config, stripMode: 'lawnmower' })
              }}
            >
              Lawnmower
            </button>
            <button
              className={`seg-btn ${config.stripMode === 'shore_first' ? 'active' : ''}`}
              onClick={() => {
                onConfigChange({ stripMode: 'shore_first' })
                onLoadField({ ...config, stripMode: 'shore_first' })
              }}
            >
              Shore-first
            </button>
          </div>

          {config.stripMode === 'lawnmower' && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 4 }}>
              Parallel rows at fixed angle. Efficient for uniform fields.
            </div>
          )}
          {config.stripMode === 'shore_first' && (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 4 }}>
              Same rows, but planted nearest the waterline first — mirrors how pioneer species naturally colonise from the tidal zone inward.
            </div>
          )}
        </div>

        <Slider
          label="Row angle"
          value={config.orientationDeg}
          min={0}
          max={175}
          step={5}
          format={(v) => `${v}°`}
          onChange={(v) => {
            onConfigChange({ orientationDeg: v })
            onLoadField({ ...config, orientationDeg: v })
          }}
          tip="Direction of flight strips in degrees from east. 0° = east-west rows; 90° = north-south rows. Align to match the dominant shoreline direction or crop row structure visible in the image."
        />
      </div>

      {/* ── Fleet ──────────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Fleet</div>

        <Slider
          label="Drones"
          value={config.nDrones}
          min={1}
          max={8}
          onChange={(v) => onConfigChange({ nDrones: v })}
          tip="Number of aircraft operating simultaneously from the dock. More drones reduce mission time but require more seed refill coordination."
        />

        <div className="field-group">
          <div className="field-label">
            <span style={{ display: 'flex', alignItems: 'center' }}>
              Dock position
              <Tip>Home base for all drones. Drones return here to recharge batteries and refill seed hoppers. Click the grid to place it on a specific cell.</Tip>
            </span>
            <span className="field-value">[{dockPos[0]}, {dockPos[1]}]</span>
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

        {dockMode !== config.dockPlacementMode &&
          onConfigChange({ dockPlacementMode: dockMode })}

        <Slider
          label="Battery life"
          value={config.batteryLifeMin}
          min={20}
          max={45}
          step={1}
          format={(v) => `${v} min`}
          onChange={(v) => onConfigChange({ batteryLifeMin: v })}
          tip="Maximum continuous flight time per battery charge. When depleted, the drone automatically returns to dock and waits for recharge before resuming."
        />

        <Slider
          label="Recharge time"
          value={config.rechargeTimeMin}
          min={0}
          max={120}
          step={5}
          format={(v) => (v === 0 ? 'instant' : `${v} min`)}
          onChange={(v) => onConfigChange({ rechargeTimeMin: v })}
          tip="Time at the dock to recharge or swap the battery. Set to 0 to simulate hot-swap or fixed battery systems. Longer recharge times reduce effective fleet utilisation."
        />
      </div>

      {/* ── Seeds ──────────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Seeds</div>

        <Slider
          label="Capacity / voyage"
          value={config.seedCapacity}
          min={1000}
          max={10000}
          step={500}
          format={(v) => v.toLocaleString()}
          onChange={(v) => onConfigChange({ seedCapacity: v })}
          tip="Maximum seeds the drone hopper carries per sortie. When exhausted, the drone returns to dock to refill before continuing. Larger hoppers reduce refill interruptions."
        />

        <Slider
          label="Seed spacing"
          value={config.seedSpacingM}
          min={0.5}
          max={3.0}
          step={0.1}
          format={(v) => `${v.toFixed(1)} m`}
          onChange={(v) => onConfigChange({ seedSpacingM: v })}
          tip="Target distance between individual seeds. Tighter spacing increases planting density and seed consumption per cell. Typical mangrove trials use 1–2 m. As cell size grows (wider field, same grid), more seeds are dropped per cell automatically."
        />
        {mPerCell && (() => {
          const seedsPerCell = Math.max(0.01, (parseFloat(mPerCell) ** 2) / (config.seedSpacingM ** 2))
          return (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: -2, marginBottom: 4 }}>
              {parseFloat(mPerCell).toFixed(1)} m/cell → {seedsPerCell >= 1
                ? `~${seedsPerCell.toFixed(1)} seeds/cell`
                : `1 seed per ~${(1 / seedsPerCell).toFixed(1)} cells`}
            </div>
          )
        })()}

        <Slider
          label="Wind speed"
          value={config.windSpeed}
          min={0}
          max={15}
          step={1}
          format={(v) => (v === 0 ? 'calm' : `${v} m/s`)}
          onChange={(v) => onConfigChange({ windSpeed: v })}
          tip="Ambient wind speed. Higher values increase seed drift from the target cell, spreading the scatter distribution. Above ~8 m/s operations are typically paused in real deployments."
        />

        <Slider
          label="Failure probability"
          value={config.failureProb}
          min={0}
          max={0.02}
          step={0.002}
          format={(v) => (v === 0 ? 'off' : `${(v * 100).toFixed(1)}%`)}
          onChange={(v) => onConfigChange({ failureProb: v })}
          tip="Per-cell chance of a random drone mechanical failure during flight. Models real-world component reliability. Failed drones are replaced by replanning remaining strips across the fleet."
        />
      </div>

      {/* ── Ecology ────────────────────────────────────────────── */}
      <div className="section">
        <div className="section-title">Ecology</div>

        <Slider
          label="Survival rate"
          value={config.survivalRate}
          min={0.1}
          max={0.8}
          step={0.05}
          format={(v) => `${Math.round(v * 100)}%`}
          onChange={(v) => onConfigChange({ survivalRate: v })}
          tip="Fraction of planted seeds expected to germinate and establish as seedlings over the first growing season. Influenced by species, substrate salinity, and tidal exposure. Typical drone-seeding trials report 15–45%."
        />


      </div>

      {/* ── Run ────────────────────────────────────────────────── */}
      <div style={{ marginTop: 'auto', paddingTop: 8 }}>
        {error && (
          <div style={{
            fontSize: 11, color: 'var(--danger)', marginBottom: 8,
            padding: '6px 8px', background: 'rgba(248,81,73,0.1)',
            borderRadius: 4, lineHeight: 1.4,
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
