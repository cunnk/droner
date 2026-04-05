import { useRef, useEffect, useState } from 'react'

// ── Cell colors ───────────────────────────────────────────────────────────────

function priorityColor(v) {
  // dark green gradient for priority values 0–1
  const g = Math.round(40 + v * 130)
  const r = Math.round(10 + v * 20)
  return `rgb(${r}, ${g}, ${Math.round(10 + v * 30)})`
}

const SIM_COLORS = {
  0: '#1e3a1a', // untouched — dark green
  1: '#b8830a', // in-progress — amber
  2: '#2e3440', // complete — dark slate
  3: '#5c1a1a', // failed — dark red
}

const DRONE_COLORS = {
  spraying:  '#4f8ef7',
  moving:    '#f0a030',
  returning: '#9c6fdb',
  charging:  '#3fb950',
  idle:      '#6e7998',
  failed:    '#f85149',
}

// ── Canvas renderer ───────────────────────────────────────────────────────────

function drawGrid(ctx, grid, mode, cellSize, opacity = 1.0) {
  const nrows = grid.length
  const ncols = grid[0]?.length ?? 0
  ctx.globalAlpha = opacity
  for (let r = 0; r < nrows; r++) {
    for (let c = 0; c < ncols; c++) {
      const v = grid[r][c]
      ctx.fillStyle = mode === 'priority'
        ? priorityColor(v)
        : (SIM_COLORS[v] ?? SIM_COLORS[0])
      ctx.fillRect(c * cellSize, r * cellSize, cellSize - 1, cellSize - 1)
    }
  }
  ctx.globalAlpha = 1.0
}

function drawDrones(ctx, drones, cellSize) {
  const r = cellSize * 0.38
  for (const d of drones) {
    if (d.state === 'failed') continue
    const [row, col] = d.position
    const cx = col * cellSize + cellSize / 2
    const cy = row * cellSize + cellSize / 2

    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, 2 * Math.PI)
    ctx.fillStyle = DRONE_COLORS[d.state] ?? DRONE_COLORS.idle
    ctx.fill()
    ctx.strokeStyle = 'rgba(255,255,255,0.8)'
    ctx.lineWidth = 1
    ctx.stroke()

    ctx.fillStyle = '#fff'
    ctx.font = `bold ${Math.max(8, cellSize * 0.45)}px Inter, sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(String(d.id), cx, cy + 0.5)
  }
}

function drawDocks(ctx, dockPositions, cellSize) {
  ctx.font = `bold ${Math.max(8, cellSize * 0.5)}px Inter, sans-serif`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (const [row, col] of dockPositions) {
    const cx = col * cellSize + cellSize / 2
    const cy = row * cellSize + cellSize / 2
    ctx.fillStyle = 'rgba(255,255,255,0.2)'
    ctx.fillRect(col * cellSize, row * cellSize, cellSize - 1, cellSize - 1)
    ctx.fillStyle = '#fff'
    ctx.fillText('D', cx, cy + 0.5)
  }
}

// ── GridCanvas ────────────────────────────────────────────────────────────────

function GridCanvas({ grid, mode, drones = [], dockPositions = [], onCellClick, cellSize = 14, imageDataUrl }) {
  const canvasRef = useRef(null)
  const imgRef = useRef(null)
  const [imgVersion, setImgVersion] = useState(0)
  const nrows = grid?.length ?? 0
  const ncols = grid?.[0]?.length ?? 0

  // Load background image whenever URL changes
  useEffect(() => {
    if (!imageDataUrl) { imgRef.current = null; setImgVersion(v => v + 1); return }
    const img = new Image()
    img.onload = () => { imgRef.current = img; setImgVersion(v => v + 1) }
    img.src = imageDataUrl
  }, [imageDataUrl])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !grid) return
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const hasBackground = imgRef.current && mode !== 'priority'
    if (hasBackground) {
      ctx.globalAlpha = 0.35
      ctx.drawImage(imgRef.current, 0, 0, canvas.width, canvas.height)
      ctx.globalAlpha = 1.0
    }

    drawGrid(ctx, grid, mode, cellSize, hasBackground ? 0.70 : 1.0)
    drawDocks(ctx, dockPositions, cellSize)
    drawDrones(ctx, drones, cellSize)
  }, [grid, mode, drones, dockPositions, cellSize, imgVersion])

  function handleClick(e) {
    if (!onCellClick) return
    const rect = canvasRef.current.getBoundingClientRect()
    const c = Math.floor((e.clientX - rect.left) / cellSize)
    const r = Math.floor((e.clientY - rect.top) / cellSize)
    if (r >= 0 && r < nrows && c >= 0 && c < ncols) onCellClick(r, c)
  }

  return (
    <canvas
      ref={canvasRef}
      className="grid-canvas"
      width={ncols * cellSize}
      height={nrows * cellSize}
      onClick={handleClick}
      style={{ cursor: onCellClick ? 'crosshair' : 'default' }}
    />
  )
}

// ── Event log ─────────────────────────────────────────────────────────────────

function EventLog({ events }) {
  const bodyRef = useRef(null)
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [events])

  if (!events.length) return null
  return (
    <div className="event-log">
      <div className="event-log-title">Events</div>
      <div className="event-log-body" ref={bodyRef}>
        {events.map((ev, i) => (
          <div
            key={i}
            className={`event-item ${
              ev.text.includes('fail') || ev.text.includes('depleted') ? 'failure' :
              ev.text.includes('eplan') ? 'replan' :
              ev.text.includes('harg') ? 'recharge' : ''
            }`}
          >
            <span className="event-ts">t={ev.t}</span>
            <span>{ev.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Metrics bar ───────────────────────────────────────────────────────────────

function MetricsBar({ metrics, planInfo, currentStep, totalSteps }) {
  if (!metrics) return null

  const cov = metrics.coverage_pct ?? 0
  const covClass = cov >= 80 ? 'good' : cov >= 50 ? 'warn' : 'bad'

  return (
    <div className="metrics-bar">
      <div className="metric">
        <div className="metric-label">Coverage</div>
        <div className={`metric-value ${covClass}`}>{cov.toFixed(1)}%</div>
      </div>
      <div className="metrics-divider" />
      <div className="metric">
        <div className="metric-label">Makespan</div>
        <div className="metric-value neutral">{metrics.makespan ?? '—'}</div>
        <div className="metric-sub">steps</div>
      </div>
      <div className="metrics-divider" />
      <div className="metric">
        <div className="metric-label">Replans</div>
        <div className={`metric-value ${(metrics.replan_count ?? 0) > 0 ? 'warn' : 'neutral'}`}>
          {metrics.replan_count ?? 0}
        </div>
      </div>
      <div className="metrics-divider" />
      <div className="metric">
        <div className="metric-label">Recovery</div>
        <div className="metric-value neutral">
          {metrics.time_to_recovery != null ? `${metrics.time_to_recovery}` : '—'}
        </div>
        <div className="metric-sub">steps</div>
      </div>
      <div style={{ flex: 1 }} />
      {planInfo && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span className="badge badge-accent">{planInfo.status}</span>
          <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
            {planInfo.n_strips} strips · {planInfo.solve_time?.toFixed(2)}s solve
          </span>
        </div>
      )}
    </div>
  )
}

// ── Player ────────────────────────────────────────────────────────────────────

function Player({ currentStep, totalSteps, playing, speed, onPlay, onPause, onStep, onSeek, onSpeedChange }) {
  return (
    <div className="player">
      <button
        className="btn btn-secondary btn-icon"
        onClick={() => onStep(-10)}
        title="Back 10"
      >⏮</button>
      <button
        className="btn btn-secondary btn-icon"
        onClick={() => onStep(-1)}
        title="Back 1"
      >◀</button>
      <button
        className="btn btn-primary btn-icon"
        style={{ width: 32, height: 28, padding: 0 }}
        onClick={playing ? onPause : onPlay}
      >
        {playing ? '⏸' : '▶'}
      </button>
      <button
        className="btn btn-secondary btn-icon"
        onClick={() => onStep(1)}
        title="Forward 1"
      >▶</button>
      <button
        className="btn btn-secondary btn-icon"
        onClick={() => onStep(10)}
        title="Forward 10"
      >⏭</button>

      <div className="player-step">
        t = {currentStep} / {totalSteps - 1}
      </div>

      <div className="player-timeline">
        <input
          type="range"
          min={0}
          max={Math.max(0, totalSteps - 1)}
          value={currentStep}
          onChange={(e) => onSeek(Number(e.target.value))}
        />
      </div>

      <div className="player-speed">
        <span>Speed</span>
        <input
          type="range"
          min={1}
          max={5}
          step={1}
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
          style={{ width: 60 }}
        />
        <span>{speed}×</span>
      </div>
    </div>
  )
}

// ── Main SimView ──────────────────────────────────────────────────────────────

export default function SimView({ field, simResult, config, isRunning, onDockPlace }) {
  const [currentStep, setCurrentStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(2)
  const intervalRef = useRef(null)

  const history = simResult?.state_history ?? null
  const totalSteps = history?.length ?? 0

  // Reset on new result
  useEffect(() => {
    setCurrentStep(0)
    setPlaying(false)
  }, [simResult])

  // Auto-play when result arrives
  useEffect(() => {
    if (simResult && totalSteps > 0) setPlaying(true)
  }, [simResult, totalSteps])

  // Playback interval
  useEffect(() => {
    clearInterval(intervalRef.current)
    if (!playing || totalSteps === 0) return

    const ms = Math.round(400 / speed)
    intervalRef.current = setInterval(() => {
      setCurrentStep((s) => {
        if (s >= totalSteps - 1) { setPlaying(false); return s }
        return s + 1
      })
    }, ms)
    return () => clearInterval(intervalRef.current)
  }, [playing, speed, totalSteps])

  // Build event list from state_history
  const events = []
  if (history) {
    for (const step of history) {
      if (step.event) events.push({ t: step.timestep, text: step.event })
    }
  }

  // Current frame
  const frame = history?.[currentStep]
  const gridToShow = frame?.grid ?? field?.grid ?? null
  const dronesToShow = frame?.drones ?? []
  const mode = history ? 'sim' : 'priority'

  // Determine cell size to fit container (approx 480px target)
  const nrows = field?.nrows ?? 32
  const ncols = field?.ncols ?? 32
  const cellSize = Math.floor(Math.min(480 / nrows, 480 / ncols, 16))

  function handleCellClick(r, c) {
    if (config.dockPlacementMode) onDockPlace([r, c])
  }

  function handleStep(delta) {
    setCurrentStep((s) => Math.max(0, Math.min(totalSteps - 1, s + delta)))
    setPlaying(false)
  }

  if (!field && !isRunning) {
    return (
      <div className="empty-state" style={{ flex: 1 }}>
        <div className="empty-icon">🛸</div>
        <div className="empty-text">Loading field…</div>
      </div>
    )
  }

  return (
    <>
      {/* Grid area */}
      <div className="grid-area">
        {gridToShow ? (
          <GridCanvas
            grid={gridToShow}
            mode={mode}
            drones={dronesToShow}
            dockPositions={config.dockPositions ?? [[0, 0]]}
            onCellClick={config.dockPlacementMode ? handleCellClick : null}
            cellSize={cellSize}
            imageDataUrl={field?.image_data_url}
          />
        ) : isRunning ? (
          <div className="empty-state">
            <div style={{ fontSize: 24 }}>⟳</div>
            <div>Running simulation…</div>
          </div>
        ) : null}

        {events.length > 0 && <EventLog events={events} />}

        {/* Legend */}
        {mode === 'sim' && (
          <div className="legend">
            {[
              { color: SIM_COLORS[0], label: 'Untouched' },
              { color: SIM_COLORS[1], label: 'Active' },
              { color: SIM_COLORS[2], label: 'Done' },
              { color: SIM_COLORS[3], label: 'Failed' },
            ].map(({ color, label }) => (
              <div key={label} className="legend-item">
                <div className="legend-dot" style={{ background: color }} />
                {label}
              </div>
            ))}
          </div>
        )}

        {mode === 'priority' && !isRunning && (
          <div className="grid-hint">
            {config.dockPlacementMode ? '🎯 Click to place dock' : 'Priority map — click ▶ Run Mission to simulate'}
          </div>
        )}
      </div>

      {/* Player (only when result exists) */}
      {history && (
        <Player
          currentStep={currentStep}
          totalSteps={totalSteps}
          playing={playing}
          speed={speed}
          onPlay={() => {
            if (currentStep >= totalSteps - 1) setCurrentStep(0)
            setPlaying(true)
          }}
          onPause={() => setPlaying(false)}
          onStep={handleStep}
          onSeek={(v) => { setCurrentStep(v); setPlaying(false) }}
          onSpeedChange={setSpeed}
        />
      )}

      {/* Metrics */}
      <MetricsBar
        metrics={simResult?.metrics}
        planInfo={simResult?.plan}
        currentStep={currentStep}
        totalSteps={totalSteps}
      />
    </>
  )
}
