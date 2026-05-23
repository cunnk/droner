import { useRef, useEffect, useState, useMemo, useCallback } from 'react'
import GIF from 'gif.js'

// ── Cell colors ────────────────────────────────────────────────────────────────

function priorityColor(v) {
  const g = Math.round(40 + v * 130)
  const r = Math.round(10 + v * 20)
  return `rgb(${r}, ${g}, ${Math.round(10 + v * 30)})`
}

const SIM_COLORS = {
  0: '#1e3a1a', // untouched — dark green
  1: '#b8830a', // in-progress — amber
  2: '#c4915a', // seeded — sandy earth / terra cotta
  3: '#5c1a1a', // failed / skipped — dark red
}

const DRONE_COLORS = {
  spraying:  '#4f8ef7',
  moving:    '#f0a030',
  returning: '#9c6fdb',
  charging:  '#3fb950',
  idle:      '#6e7998',
  failed:    '#f85149',
}

// ── Mask data URL generator ────────────────────────────────────────────────────

function buildMaskDataUrl(soilMask) {
  if (!soilMask || !soilMask.length) return null
  const nrows = soilMask.length
  const ncols = soilMask[0]?.length ?? 0
  if (!ncols) return null
  const canvas = document.createElement('canvas')
  canvas.width = ncols
  canvas.height = nrows
  const ctx = canvas.getContext('2d')
  const id = ctx.createImageData(ncols, nrows)
  for (let r = 0; r < nrows; r++) {
    for (let c = 0; c < ncols; c++) {
      const i = (r * ncols + c) * 4
      if (soilMask[r][c]) {
        // plantable mudflat — semi-transparent green
        id.data[i] = 46; id.data[i + 1] = 160; id.data[i + 2] = 67; id.data[i + 3] = 200
      } else {
        // excluded (water / canopy) — semi-transparent red
        id.data[i] = 190; id.data[i + 1] = 40; id.data[i + 2] = 40; id.data[i + 3] = 160
      }
    }
  }
  ctx.putImageData(id, 0, 0)
  return canvas.toDataURL()
}

// ── Scale bar ─────────────────────────────────────────────────────────────────

function drawScaleBar(ctx, fieldWidthM, ncols, cellSize, canvasW, canvasH) {
  if (!fieldWidthM || fieldWidthM <= 0 || !ncols) return
  const metersPerCell = fieldWidthM / ncols
  const pixelsPerMeter = cellSize / metersPerCell

  // Pick nicest round bar length that renders ~60–120 px
  const niceM = [2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000]
  const targetM = 80 / pixelsPerMeter
  const barM = niceM.find(v => v >= targetM) ?? niceM[niceM.length - 1]
  const barPx = Math.round(barM * pixelsPerMeter)

  const mx = 8, my = 8          // margin from bottom-left
  const x = mx
  const lineY = canvasH - my - 3
  const topY  = lineY - 5

  // Background pill
  ctx.fillStyle = 'rgba(0,0,0,0.55)'
  ctx.beginPath()
  ctx.rect(x - 3, topY - 2, barPx + 6, lineY - topY + 7)
  ctx.fill()

  // Scale line
  ctx.strokeStyle = '#ffffff'
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(x, lineY)
  ctx.lineTo(x + barPx, lineY)
  ctx.moveTo(x, topY)
  ctx.lineTo(x, lineY + 3)
  ctx.moveTo(x + barPx, topY)
  ctx.lineTo(x + barPx, lineY + 3)
  ctx.stroke()

  // Label
  const label = barM >= 1000 ? `${barM / 1000} km` : `${barM} m`
  ctx.fillStyle = '#ffffff'
  ctx.font = '9px Inter, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'bottom'
  ctx.fillText(label, x + barPx / 2, lineY - 1)
}

// ── Canvas renderer ────────────────────────────────────────────────────────────

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

// ── GridCanvas ─────────────────────────────────────────────────────────────────

function GridCanvas({
  grid, mode, drones = [], dockPositions = [], onCellClick,
  cellSize = 14, imageDataUrl, maskDataUrl, overlayView,
  seedDrops = [], fieldWidthM = 0,
}) {
  const canvasRef = useRef(null)
  const imgRef    = useRef(null)
  const maskRef   = useRef(null)
  const [imgV,  setImgV]  = useState(0)
  const [maskV, setMaskV] = useState(0)
  const nrows = grid?.length ?? 0
  const ncols = grid?.[0]?.length ?? 0

  // Load background image
  useEffect(() => {
    if (!imageDataUrl) { imgRef.current = null; setImgV(v => v + 1); return }
    const img = new Image()
    img.onload = () => { imgRef.current = img; setImgV(v => v + 1) }
    img.src = imageDataUrl
  }, [imageDataUrl])

  // Load mask image
  useEffect(() => {
    if (!maskDataUrl) { maskRef.current = null; setMaskV(v => v + 1); return }
    const img = new Image()
    img.onload = () => { maskRef.current = img; setMaskV(v => v + 1) }
    img.src = maskDataUrl
  }, [maskDataUrl])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !grid) return
    const ctx = canvas.getContext('2d')
    const cw = canvas.width
    const ch = canvas.height
    ctx.clearRect(0, 0, cw, ch)

    const hasImg  = !!imgRef.current
    const hasMask = !!maskRef.current

    if (overlayView === 'image' && hasImg) {
      // Raw image only, full opacity
      ctx.globalAlpha = 1.0
      ctx.drawImage(imgRef.current, 0, 0, cw, ch)
      ctx.globalAlpha = 1.0
    } else if (overlayView === 'mask' && hasMask) {
      // Grid at low opacity + mask overlay
      drawGrid(ctx, grid, mode, cellSize, 0.25)
      ctx.globalAlpha = 0.75
      ctx.drawImage(maskRef.current, 0, 0, cw, ch)
      ctx.globalAlpha = 1.0
    } else if (overlayView === 'compare' && hasImg && hasMask) {
      // Raw image + mask overlay side-by-side in opacity
      ctx.globalAlpha = 0.65
      ctx.drawImage(imgRef.current, 0, 0, cw, ch)
      ctx.globalAlpha = 0.6
      ctx.drawImage(maskRef.current, 0, 0, cw, ch)
      ctx.globalAlpha = 1.0
    } else {
      // Default: grid with faint background image
      if (hasImg && mode !== 'priority') {
        ctx.globalAlpha = 0.35
        ctx.drawImage(imgRef.current, 0, 0, cw, ch)
        ctx.globalAlpha = 1.0
      }
      drawGrid(ctx, grid, mode, cellSize, hasImg && mode !== 'priority' ? 0.70 : 1.0)
    }

    drawDocks(ctx, dockPositions, cellSize)

    // Seed scatter overlay
    if (seedDrops.length > 0) {
      ctx.globalAlpha = 0.55
      ctx.fillStyle = '#3fb950'
      for (const drop of seedDrops) {
        const [ar, ac] = drop.actual ?? drop.nominal ?? []
        if (ar == null) continue
        const px = ac * cellSize + cellSize / 2
        const py = ar * cellSize + cellSize / 2
        ctx.beginPath()
        ctx.arc(px, py, 1.5, 0, 2 * Math.PI)
        ctx.fill()
      }
      ctx.globalAlpha = 1.0
    }

    if (overlayView === 'grid' || !overlayView) {
      drawDrones(ctx, drones, cellSize)
    } else if (overlayView !== 'image') {
      drawDrones(ctx, drones, cellSize)
    }

    // Scale bar (always on top)
    drawScaleBar(ctx, fieldWidthM, ncols, cellSize, cw, ch)
  }, [grid, mode, drones, dockPositions, cellSize, imgV, maskV, seedDrops, overlayView, fieldWidthM, ncols])

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

function MetricsBar({ metrics, planInfo, survivalRate }) {
  if (!metrics) return null
  const seeds    = metrics.total_seeds_planted ?? 0
  const cellsPct = metrics.coverage_pct ?? 0
  const survivors = metrics.expected_survivors ?? Math.round(seeds * (survivalRate ?? 0.4))
  const refills  = metrics.dock_returns_for_seeds ?? 0
  return (
    <div className="metrics-bar">
      <div className="metric">
        <div className="metric-label">Seeds planted</div>
        <div className={`metric-value ${seeds > 0 ? 'good' : 'neutral'}`}>{seeds.toLocaleString()}</div>
      </div>
      <div className="metrics-divider" />
      <div className="metric">
        <div className="metric-label">Cells seeded</div>
        <div className={`metric-value ${cellsPct >= 80 ? 'good' : cellsPct >= 50 ? 'warn' : 'bad'}`}>
          {cellsPct.toFixed(1)}%
        </div>
        <div className="metric-sub">plantable</div>
      </div>
      <div className="metrics-divider" />
      <div className="metric">
        <div className="metric-label">Exp. survivors</div>
        <div className="metric-value neutral">{survivors.toLocaleString()}</div>
      </div>
      <div className="metrics-divider" />
      <div className="metric">
        <div className="metric-label">Refill returns</div>
        <div className={`metric-value ${refills > 0 ? 'warn' : 'neutral'}`}>{refills}</div>
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

function Player({ currentStep, totalSteps, playing, speed, onPlay, onPause, onStep, onSeek, onSpeedChange, onExportGif, exporting }) {
  return (
    <div className="player">
      <button className="btn btn-secondary btn-icon" onClick={() => onStep(-10)} title="Back 10">⏮</button>
      <button className="btn btn-secondary btn-icon" onClick={() => onStep(-1)}  title="Back 1">◀</button>
      <button
        className="btn btn-primary btn-icon"
        style={{ width: 32, height: 28, padding: 0 }}
        onClick={playing ? onPause : onPlay}
      >
        {playing ? '⏸' : '▶'}
      </button>
      <button className="btn btn-secondary btn-icon" onClick={() => onStep(1)}   title="Forward 1">▶</button>
      <button className="btn btn-secondary btn-icon" onClick={() => onStep(10)}  title="Forward 10">⏭</button>
      <div className="player-step">t = {currentStep} / {totalSteps - 1}</div>
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
          type="range" min={1} max={5} step={1} value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
          style={{ width: 60 }}
        />
        <span>{speed}×</span>
      </div>
      <button
        className="btn btn-secondary"
        style={{ fontSize: 10, padding: '3px 8px', whiteSpace: 'nowrap', opacity: exporting ? 0.6 : 1 }}
        onClick={onExportGif}
        disabled={exporting}
        title="Download animation as GIF"
      >
        {exporting ? 'Encoding…' : '⬇ GIF'}
      </button>
    </div>
  )
}

// ── Overlay controls ──────────────────────────────────────────────────────────

const OVERLAY_OPTIONS = [
  { key: 'grid',    label: 'Grid' },
  { key: 'image',   label: 'Image' },
  { key: 'mask',    label: 'Mask' },
  { key: 'compare', label: 'Overlay' },
]

function OverlayControls({ value, onChange, hasMask }) {
  if (!hasMask) return null
  return (
    <div style={{
      position: 'absolute', top: 6, left: 8, zIndex: 3,
      display: 'flex', gap: 2,
      background: 'rgba(0,0,0,0.6)', borderRadius: 5, padding: '2px 3px',
    }}>
      {OVERLAY_OPTIONS.map(opt => (
        <button
          key={opt.key}
          onClick={() => onChange(opt.key)}
          style={{
            fontSize: 9, padding: '2px 6px', borderRadius: 3,
            border: 'none', cursor: 'pointer',
            background: value === opt.key ? 'var(--accent, #4f8ef7)' : 'transparent',
            color: value === opt.key ? '#fff' : 'var(--text-dim)',
            fontWeight: value === opt.key ? 600 : 400,
          }}
        >
          {opt.label}
        </button>
      ))}
      {value !== 'grid' && (
        <span style={{ fontSize: 9, color: 'var(--text-dim)', padding: '2px 4px', alignSelf: 'center' }}>
          {value === 'image' ? '· raw aerial image' :
           value === 'mask'  ? '· green = plantable, red = excluded' :
                               '· image + mask overlay'}
        </span>
      )}
    </div>
  )
}

// ── Main SimView ──────────────────────────────────────────────────────────────

export default function SimView({ field, simResult, config, isRunning, onDockPlace }) {
  const [currentStep, setCurrentStep] = useState(0)
  const [playing,     setPlaying]     = useState(false)
  const [speed,       setSpeed]       = useState(2)
  const [showSeeds,   setShowSeeds]   = useState(false)
  const [overlayView, setOverlayView] = useState('grid')
  const [exporting,   setExporting]   = useState(false)
  const intervalRef  = useRef(null)
  const canvasRef    = useRef(null)

  const history    = simResult?.state_history ?? null
  const totalSteps = history?.length ?? 0

  useEffect(() => { setCurrentStep(0); setPlaying(false) }, [simResult])
  useEffect(() => { if (simResult && totalSteps > 0) setPlaying(true) }, [simResult, totalSteps])

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

  // Build event list
  const events = []
  if (history) {
    for (const step of history) {
      if (step.event) events.push({ t: step.timestep, text: step.event })
    }
  }

  const frame       = history?.[currentStep]
  const gridToShow  = frame?.grid ?? field?.grid ?? null
  const dronesToShow = frame?.drones ?? []
  const mode        = history ? 'sim' : 'priority'

  // Accumulate seed drops up to current step
  const accumulatedSeeds = useMemo(() => {
    if (!showSeeds || !history) return []
    const out = []
    for (let i = 0; i <= currentStep && i < history.length; i++) {
      const drops = history[i].seed_drops
      if (drops) out.push(...drops)
    }
    return out
  }, [showSeeds, history, currentStep])

  // Generate mask data URL from soil_mask boolean array
  const maskDataUrl = useMemo(() => buildMaskDataUrl(field?.soil_mask), [field?.soil_mask])
  const hasMask = !!maskDataUrl

  const nrows    = field?.nrows ?? 32
  const ncols    = field?.ncols ?? 32
  const cellSize = Math.floor(Math.min(480 / nrows, 480 / ncols, 16))

  // GIF export: render every frame offscreen and encode
  const handleExportGif = useCallback(() => {
    if (!history || history.length === 0) return
    setExporting(true)
    setPlaying(false)

    const cw = ncols * cellSize
    const ch = nrows * cellSize

    const offscreen = document.createElement('canvas')
    offscreen.width  = cw
    offscreen.height = ch
    const ctx = offscreen.getContext('2d')

    // Load background image once
    const bgImg = new Image()
    const bgSrc = field?.image_data_url ?? null

    const encode = (bgImage) => {
      const gif = new GIF({
        workers: 2,
        quality: 6,
        workerScript: '/gif.worker.js',
        width: cw,
        height: ch,
      })

      // Sample every Nth frame to keep file size manageable
      const step = Math.max(1, Math.floor(history.length / 120))

      for (let i = 0; i < history.length; i += step) {
        ctx.clearRect(0, 0, cw, ch)
        if (bgImage) {
          ctx.globalAlpha = 0.35
          ctx.drawImage(bgImage, 0, 0, cw, ch)
          ctx.globalAlpha = 1.0
        }
        const frame = history[i]
        drawGrid(ctx, frame.grid, 'sim', cellSize, bgImage ? 0.70 : 1.0)
        drawDocks(ctx, config.dockPositions ?? [[0, 0]], cellSize)
        drawDrones(ctx, frame.drones ?? [], cellSize)
        drawScaleBar(ctx, config.fieldWidthM ?? 0, ncols, cellSize, cw, ch)
        gif.addFrame(ctx, { copy: true, delay: Math.round(400 / 2) })
      }

      gif.on('finished', (blob) => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = 'mangrove_mission.gif'
        a.click()
        URL.revokeObjectURL(url)
        setExporting(false)
      })

      gif.render()
    }

    if (bgSrc) {
      const img = new Image()
      img.onload = () => encode(img)
      img.onerror = () => encode(null)
      img.src = bgSrc
    } else {
      encode(null)
    }
  }, [history, ncols, nrows, cellSize, field, config])

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
        <div className="empty-text">Upload a field image to get started</div>
      </div>
    )
  }

  return (
    <>
      {/* Grid area */}
      <div className="grid-area">
        {/* Overlay controls — top left */}
        <OverlayControls value={overlayView} onChange={setOverlayView} hasMask={hasMask} />

        {/* Seed scatter toggle — top right */}
        {history && (
          <div style={{
            position: 'absolute', top: 6, right: 8, zIndex: 2,
            display: 'flex', alignItems: 'center', gap: 5,
            background: 'rgba(0,0,0,0.55)', borderRadius: 4, padding: '3px 7px',
          }}>
            <label style={{
              display: 'flex', alignItems: 'center', gap: 4,
              cursor: 'pointer', fontSize: 10, color: 'var(--text-muted)', userSelect: 'none',
            }}>
              <input
                type="checkbox"
                checked={showSeeds}
                onChange={e => setShowSeeds(e.target.checked)}
                style={{ margin: 0 }}
              />
              Show seed scatter
            </label>
          </div>
        )}

        {gridToShow ? (
          <GridCanvas
            grid={gridToShow}
            mode={mode}
            drones={dronesToShow}
            dockPositions={config.dockPositions ?? [[0, 0]]}
            onCellClick={config.dockPlacementMode ? handleCellClick : null}
            cellSize={cellSize}
            imageDataUrl={field?.image_data_url}
            maskDataUrl={maskDataUrl}
            overlayView={overlayView}
            seedDrops={accumulatedSeeds}
            fieldWidthM={config.fieldWidthM ?? 0}
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
              { color: SIM_COLORS[0], label: 'Unplanted mudflat' },
              { color: SIM_COLORS[1], label: 'Seeding in progress' },
              { color: SIM_COLORS[2], label: '🌱 Seeded' },
              { color: SIM_COLORS[3], label: 'Skipped (water / canopy)' },
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
            {config.dockPlacementMode
              ? '🎯 Click to place dock'
              : hasMask
                ? 'Use overlay controls ↖ to compare image & mask'
                : 'Soil priority — click ▶ Run Mission to simulate'}
          </div>
        )}
      </div>

      {/* Player */}
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
          onExportGif={handleExportGif}
          exporting={exporting}
        />
      )}

      {/* Metrics */}
      <MetricsBar
        metrics={simResult?.metrics}
        planInfo={simResult?.plan}
        survivalRate={config?.survivalRate}
      />

    </>
  )
}
