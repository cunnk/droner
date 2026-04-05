import { useState, useRef, useCallback, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts'

// ── Simple markdown renderer ──────────────────────────────────────────────────

function renderInline(text) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**'))
      return <strong key={i}>{p.slice(2, -2)}</strong>
    if (p.startsWith('*') && p.endsWith('*'))
      return <em key={i}>{p.slice(1, -1)}</em>
    return p
  })
}

function SimpleMarkdown({ text, style }) {
  if (!text) return null
  const lines = text.split('\n')
  const elements = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('### ')) {
      elements.push(<div key={i} style={{ fontWeight: 700, fontSize: 12, marginTop: 6, color: 'var(--text)' }}>{line.slice(4)}</div>)
    } else if (line.startsWith('## ')) {
      elements.push(<div key={i} style={{ fontWeight: 700, fontSize: 13, marginTop: 8, color: 'var(--text)' }}>{line.slice(3)}</div>)
    } else if (line.startsWith('# ')) {
      elements.push(<div key={i} style={{ fontWeight: 700, fontSize: 14, marginTop: 8, color: 'var(--text)' }}>{line.slice(2)}</div>)
    } else if (line.match(/^[-*] /)) {
      const items = []
      while (i < lines.length && lines[i].match(/^[-*] /)) {
        items.push(<li key={i} style={{ marginBottom: 2 }}>{renderInline(lines[i].slice(2))}</li>)
        i++
      }
      elements.push(<ul key={`ul-${i}`} style={{ margin: '4px 0 4px 16px', padding: 0 }}>{items}</ul>)
      continue
    } else if (line.trim()) {
      elements.push(<p key={i} style={{ margin: '3px 0', lineHeight: 1.6 }}>{renderInline(line)}</p>)
    }
    i++
  }
  return <div style={style}>{elements}</div>
}

// ── Cell colors ───────────────────────────────────────────────────────────────

function priorityColor(v) {
  return `rgb(${Math.round(10 + v * 20)}, ${Math.round(40 + v * 130)}, ${Math.round(10 + v * 30)})`
}

const SIM_COLORS = { 0: '#1e3a1a', 1: '#b8830a', 2: '#2e3440', 3: '#5c1a1a' }
const DRONE_COLORS = {
  spraying: '#4f8ef7', moving: '#f0a030', returning: '#9c6fdb',
  charging: '#3fb950', idle: '#6e7998', failed: '#f85149',
}

// ── Mini grid ─────────────────────────────────────────────────────────────────

function GridMini({ grid, drones = [], dockPositions = [], cellSize = 10,
                    imageDataUrl, mandatoryCells = [], eventCells = [] }) {
  const canvasRef = useRef(null)
  const imgRef = useRef(null)
  const [imgVersion, setImgVersion] = useState(0)
  const nrows = grid?.length ?? 0
  const ncols = grid?.[0]?.length ?? 0

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

    // Background image
    if (imgRef.current) {
      ctx.globalAlpha = 0.35
      ctx.drawImage(imgRef.current, 0, 0, canvas.width, canvas.height)
      ctx.globalAlpha = 1.0
    }

    // Grid cells
    const isSimGrid = grid.some(row => row.some(v => Number.isInteger(v) && v > 1))
    ctx.globalAlpha = imgRef.current && isSimGrid ? 0.70 : 1.0
    for (let r = 0; r < nrows; r++) {
      for (let c = 0; c < ncols; c++) {
        const v = grid[r][c]
        ctx.fillStyle = isSimGrid ? (SIM_COLORS[v] ?? SIM_COLORS[0]) : priorityColor(v)
        ctx.fillRect(c * cellSize, r * cellSize, cellSize - 1, cellSize - 1)
      }
    }
    ctx.globalAlpha = 1.0

    // Mandatory zone overlay — amber tint
    if (mandatoryCells.length > 0) {
      ctx.globalAlpha = 0.28
      ctx.fillStyle = '#f0a030'
      for (const [r, c] of mandatoryCells) {
        ctx.fillRect(c * cellSize, r * cellSize, cellSize - 1, cellSize - 1)
      }
      ctx.globalAlpha = 1.0
      ctx.strokeStyle = '#f0a030'
      ctx.lineWidth = 0.8
      ctx.globalAlpha = 0.6
      for (const [r, c] of mandatoryCells) {
        ctx.strokeRect(c * cellSize + 0.5, r * cellSize + 0.5, cellSize - 2, cellSize - 2)
      }
      ctx.globalAlpha = 1.0
    }

    // Event cell markers — bug emoji (🐛 alive, 💀 sprayed)
    const bugSize = Math.max(8, cellSize * 0.75)
    ctx.font = `${bugSize}px serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    for (const [r, c] of eventCells) {
      const cx = c * cellSize + cellSize / 2
      const cy = r * cellSize + cellSize / 2
      const isSprayed = isSimGrid && grid[r]?.[c] === 2
      ctx.globalAlpha = isSprayed ? 0.55 : 1.0
      ctx.fillText(isSprayed ? '💀' : '🐛', cx, cy + 0.5)
    }
    ctx.globalAlpha = 1.0

    // Docks
    for (const [row, col] of dockPositions) {
      ctx.fillStyle = 'rgba(255,255,255,0.15)'
      ctx.fillRect(col * cellSize, row * cellSize, cellSize - 1, cellSize - 1)
    }

    // Drones
    const r = cellSize * 0.35
    for (const d of drones) {
      if (d.state === 'failed') continue
      const [row, col] = d.position
      const cx = col * cellSize + cellSize / 2
      const cy = row * cellSize + cellSize / 2
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, 2 * Math.PI)
      ctx.fillStyle = DRONE_COLORS[d.state] ?? DRONE_COLORS.idle
      ctx.fill()
    }
  }, [grid, drones, dockPositions, cellSize, imgVersion, mandatoryCells, eventCells, nrows, ncols])

  return (
    <canvas ref={canvasRef} width={ncols * cellSize} height={nrows * cellSize}
      style={{ imageRendering: 'pixelated' }} />
  )
}

// ── Decision log entry ────────────────────────────────────────────────────────

function LogEntry({ entry }) {
  const [expanded, setExpanded] = useState(false)

  if (entry.type === 'text') {
    return (
      <div style={{
        padding: '8px 12px', background: 'rgba(79,142,247,0.08)',
        borderLeft: '2px solid var(--accent)', borderRadius: '0 4px 4px 0',
        fontSize: 12, lineHeight: 1.6, color: 'var(--text)', marginBottom: 6,
      }}>
        {entry.text}
      </div>
    )
  }

  if (entry.type === 'system') {
    const isStorm = entry.text.includes('Storm') || entry.text.includes('⛈') || entry.text.includes('⚠')
    return (
      <div style={{
        fontSize: 11, padding: '4px 8px', marginBottom: 4, borderRadius: 4,
        background: isStorm ? 'rgba(248,81,73,0.12)' : 'transparent',
        color: isStorm ? 'var(--danger)' : 'var(--text-dim)',
        fontWeight: isStorm ? 600 : 400,
        border: isStorm ? '1px solid rgba(248,81,73,0.25)' : 'none',
      }}>
        {entry.text}
      </div>
    )
  }

  if (entry.type === 'tool_call') {
    const argsStr = JSON.stringify(entry.input, null, 2)
    const short = Object.entries(entry.input)
      .filter(([k]) => k !== 'run_id')
      .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
      .join(', ')
    return (
      <div style={{
        background: 'var(--panel2)', border: '1px solid var(--border)',
        borderRadius: 4, marginBottom: 4, overflow: 'hidden',
      }}>
        <div style={{
          padding: '5px 10px', display: 'flex', gap: 8, alignItems: 'center',
          cursor: argsStr.length > 40 ? 'pointer' : 'default',
        }} onClick={() => setExpanded(!expanded)}>
          <span style={{ color: 'var(--accent)', fontFamily: 'monospace', fontSize: 11 }}>
            → {entry.name}
          </span>
          {short && <span style={{ color: 'var(--text-dim)', fontFamily: 'monospace', fontSize: 10 }}>({short})</span>}
          {argsStr.length > 60 && (
            <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-dim)' }}>{expanded ? '▲' : '▼'}</span>
          )}
        </div>
        {expanded && (
          <pre style={{
            margin: 0, padding: '6px 10px', borderTop: '1px solid var(--border)',
            fontSize: 10, color: 'var(--text-muted)', overflowX: 'auto',
          }}>{argsStr}</pre>
        )}
      </div>
    )
  }

  if (entry.type === 'tool_result') {
    const highlight = ['coverage_pct', 'priority_coverage', 'strips_assigned', 'strips_deferred',
      'status', 'alert', 'minutes_remaining', 'mandatory_count', 'makespan', 'timesteps', 'note']
    const items = Object.entries(entry.result).filter(([k]) => highlight.includes(k)).slice(0, 5)
    return (
      <div style={{ padding: '4px 10px 6px', marginBottom: 6, borderLeft: '2px solid var(--border2)' }}>
        {items.map(([k, v]) => (
          <div key={k} style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.7 }}>
            <span style={{ color: 'var(--text-dim)' }}>{k}: </span>
            <span style={{
              color: k === 'alert' && v ? 'var(--danger)' : k.includes('coverage') ? 'var(--success)' : 'var(--text)',
              fontWeight: 600,
            }}>
              {typeof v === 'number'
                ? (k.includes('pct') || k.includes('coverage') ? `${Number(v).toFixed(1)}%` : v)
                : String(v)}
            </span>
          </div>
        ))}
      </div>
    )
  }
  return null
}

// ── Grouped bar chart for baseline vs AI comparison ───────────────────────────

function MetricsChart({ bl, ad }) {
  if (!bl && !ad) return null

  const data = [
    {
      metric: 'Overall\nCoverage',
      shortMetric: 'Coverage',
      Baseline: bl?.coverage_pct != null ? +bl.coverage_pct.toFixed(1) : null,
      AI: ad?.coverage_pct != null ? +ad.coverage_pct.toFixed(1) : null,
    },
    {
      metric: 'Priority\nZones ★',
      shortMetric: 'Priority ★',
      Baseline: bl?.top_priority_coverage != null ? +(bl.top_priority_coverage * 100).toFixed(1) : null,
      AI: ad?.top_priority_coverage != null ? +(ad.top_priority_coverage * 100).toFixed(1) : null,
    },
  ].filter(d => d.Baseline != null || d.AI != null)

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    return (
      <div style={{
        background: 'var(--panel2)', border: '1px solid var(--border)',
        borderRadius: 5, padding: '6px 10px', fontSize: 11,
      }}>
        <div style={{ color: 'var(--text-muted)', marginBottom: 3 }}>{label}</div>
        {payload.map(p => (
          <div key={p.name} style={{ color: p.color }}>
            {p.name}: <strong>{p.value}%</strong>
          </div>
        ))}
        {payload.length === 2 && payload[0].value != null && payload[1].value != null && (
          <div style={{ color: 'var(--text-dim)', marginTop: 3, borderTop: '1px solid var(--border)', paddingTop: 3 }}>
            Δ {payload[1].value > payload[0].value ? '+' : ''}{(payload[1].value - payload[0].value).toFixed(1)}pp
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ padding: '8px 10px 4px', borderTop: '1px solid var(--border)' }}>
      <div style={{
        fontSize: 9, fontWeight: 600, color: 'var(--text-dim)',
        textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4,
      }}>
        At storm deadline — Baseline vs AI
      </div>
      <ResponsiveContainer width="100%" height={90}>
        <BarChart data={data} barCategoryGap="30%" barGap={3}
          margin={{ top: 4, right: 6, left: -22, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="shortMetric"
            tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
            tickLine={false} axisLine={{ stroke: 'var(--border)' }} />
          <YAxis domain={[0, 100]}
            tick={{ fill: 'var(--text-muted)', fontSize: 9 }}
            tickLine={false} axisLine={false}
            tickFormatter={v => `${v}%`} />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            iconSize={8} iconType="square"
            wrapperStyle={{ fontSize: 10, paddingTop: 2 }}
          />
          <Bar dataKey="Baseline" fill="#6e7998" fillOpacity={0.85} radius={[2, 2, 0, 0]} maxBarSize={28} />
          <Bar dataKey="AI" fill="#7c4ef7" fillOpacity={0.9} radius={[2, 2, 0, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>

      {/* Takeaway */}
      {bl && ad && (() => {
        const deltaP = (ad.top_priority_coverage ?? 0) - (bl.top_priority_coverage ?? 0)
        const deltaC = (ad.coverage_pct ?? 0) - (bl.coverage_pct ?? 0)
        if (Math.abs(deltaP) < 0.01 && Math.abs(deltaC) < 1) {
          return (
            <div style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.5, marginTop: 2 }}>
              Both plans performed similarly by deadline.
            </div>
          )
        }
        return (
          <div style={{ fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.5, marginTop: 2 }}>
            {deltaP > 0.01
              ? `✦ AI secured ${(deltaP * 100).toFixed(1)}pp more priority coverage`
              : `Overall coverage ${deltaC >= 0 ? '+' : ''}${deltaC.toFixed(1)}pp`}
            {deltaP > 0.01 && deltaC < -1 ? `, trading ${Math.abs(deltaC).toFixed(1)}pp overall.` : '.'}
          </div>
        )
      })()}

      {/* Legend */}
      <div style={{ display: 'flex', gap: 8, marginTop: 4, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 9, color: 'var(--text-dim)' }}>
          <div style={{ width: 10, height: 10, background: 'rgba(240,160,48,0.5)', border: '1px solid #f0a030', borderRadius: 1 }} />
          Mandatory zones
        </div>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 9, color: 'var(--text-dim)' }}>
          <span style={{ fontSize: 11 }}>🐛</span> Pest event
        </div>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 9, color: 'var(--text-dim)' }}>
          <span style={{ fontSize: 11 }}>💀</span> Sprayed
        </div>
      </div>
    </div>
  )
}

// ── Field events table ────────────────────────────────────────────────────────

function EventsTable({ eventCells, visibleCount, currentStep, grid }) {
  const [open, setOpen] = useState(false)
  if (!eventCells?.length) return null

  const isSimGrid = grid?.some(row => row.some(v => Number.isInteger(v) && v > 1))
  const revealed = visibleCount ?? eventCells.length

  return (
    <div style={{ borderTop: '1px solid var(--border)', background: 'var(--panel2)' }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          padding: '5px 12px', cursor: 'pointer', display: 'flex',
          alignItems: 'center', gap: 6, userSelect: 'none',
        }}
      >
        <span style={{ fontSize: 11 }}>🐛</span>
        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', flex: 1 }}>
          Field Events
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)', marginRight: 4 }}>
          {revealed}/{eventCells.length} reported
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div style={{ padding: '0 12px 8px', maxHeight: 120, overflowY: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                {['t=', 'Row', 'Col', 'Status'].map(h => (
                  <th key={h} style={{
                    textAlign: 'left', padding: '2px 4px', color: 'var(--text-dim)',
                    fontWeight: 600, fontSize: 10, borderBottom: '1px solid var(--border)',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {eventCells.map((ec, i) => {
                const [r, c, evType, severity, evTs] = ec
                const isVisible = (evTs ?? 0) <= (currentStep ?? Infinity)
                const cellVal = isVisible ? grid?.[r]?.[c] : null
                const status = !isVisible ? '🔒 not yet'
                  : !isSimGrid ? '—'
                  : cellVal === 2 ? '💀 sprayed'
                  : cellVal === 1 ? '⚡ active'
                  : cellVal === 3 ? '✗ failed'
                  : '🐛 untouched'
                return (
                  <tr key={i} style={{
                    background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)',
                    opacity: isVisible ? 1 : 0.4,
                  }}>
                    <td style={{ padding: '3px 4px', color: 'var(--text-dim)', fontFamily: 'monospace' }}>{evTs ?? '—'}</td>
                    <td style={{ padding: '3px 4px', color: 'var(--text)' }}>{r}</td>
                    <td style={{ padding: '3px 4px', color: 'var(--text)' }}>{c}</td>
                    <td style={{ padding: '3px 4px', color: cellVal === 2 ? 'var(--success)' : 'var(--text-muted)' }}>{status}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── Main MCPView ──────────────────────────────────────────────────────────────

export default function MCPView({ field, config, simResult, onMcpDone }) {
  const [weatherEnabled, setWeatherEnabled] = useState(true)
  const [weatherMinutes, setWeatherMinutes] = useState(8)
  const [fieldEventsEnabled, setFieldEventsEnabled] = useState(false)
  const [nFieldEvents, setNFieldEvents] = useState(3)

  const [log, setLog] = useState([])
  const [summary, setSummary] = useState(null)
  const [running, setRunning] = useState(false)
  const [done, setDone] = useState(false)
  const [result, setResult] = useState(null)
  // result shape: { state_history, baseline_at_deadline, adapted_at_deadline,
  //                mandatory_cells, event_cells, storm_arrival_timestep, weather_minutes }

  const [currentStep, setCurrentStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const intervalRef = useRef(null)
  const logRef = useRef(null)
  const cleanupRef = useRef(null)

  const history = result?.state_history ?? null
  const totalSteps = history?.length ?? 0
  const stormTs = result?.storm_arrival_timestep ?? null
  const mandatoryCells = result?.mandatory_cells ?? []
  const eventCells = result?.event_cells ?? []

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  useEffect(() => {
    clearInterval(intervalRef.current)
    if (!playing || totalSteps === 0) return
    // Stop at storm arrival if one is set, otherwise at end of history
    const stopAt = stormTs != null ? stormTs : totalSteps - 1
    intervalRef.current = setInterval(() => {
      setCurrentStep(s => {
        if (s >= stopAt) { setPlaying(false); return stopAt }
        return s + 1
      })
    }, 120)
    return () => clearInterval(intervalRef.current)
  }, [playing, totalSteps, stormTs])

  useEffect(() => {
    if (result) { setCurrentStep(0); setPlaying(true) }
  }, [result])

  const apiKey = typeof localStorage !== 'undefined'
    ? localStorage.getItem('drone_anthropic_api_key') : null

  const handleRun = useCallback(() => {
    if (!field) return
    if (!apiKey) {
      setLog([{ type: 'system', text: '⚠ No API key set. Open Settings (⚙) and add your Anthropic API key.' }])
      return
    }
    cleanupRef.current?.()
    setLog([])
    setSummary(null)
    setDone(false)
    setResult(null)
    setRunning(true)

    const controller = new AbortController()
    cleanupRef.current = () => controller.abort()

    const body = {
      grid: field.grid, nrows: field.nrows, ncols: field.ncols,
      n_drones: config.nDrones ?? 3,
      dock_positions: config.dockPositions ?? [[0, 0]],
      orientation_deg: config.orientationDeg ?? 0,
      seconds_per_cell: config.secondsPerCell ?? 2.0,
      battery_drain: config.batteryDrain ?? 0,
      weather_enabled: weatherEnabled, weather_minutes: weatherMinutes,
      field_events_enabled: fieldEventsEnabled, n_field_events: nFieldEvents,
      api_key: apiKey,
    }

    fetch('/api/mcp/adapt/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: controller.signal,
    })
      .then(async res => {
        if (!res.ok) throw new Error(await res.text())
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { done: rd, value } = await reader.read()
          if (rd) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop()
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'done') {
                setResult(data)
                setDone(true)
                setRunning(false)
                onMcpDone?.()
              } else if (data.type === 'summary') {
                setSummary(data.text)
              } else {
                setLog(prev => [...prev, data])
              }
            } catch {}
          }
        }
      })
      .catch(err => {
        if (err.name !== 'AbortError') {
          setLog(prev => [...prev, { type: 'system', text: `Error: ${err.message}` }])
          setRunning(false)
        }
      })
  }, [field, config, weatherEnabled, weatherMinutes, fieldEventsEnabled, nFieldEvents, apiKey, onMcpDone])

  const frame = history?.[currentStep]
  const nrows = field?.nrows ?? 32
  const ncols = field?.ncols ?? 32
  const cellSize = Math.floor(Math.min(300 / nrows, 300 / ncols, 13))

  const frameEvent = frame?.event ?? null
  const isStormEvent = frameEvent && (frameEvent.includes('Storm') || frameEvent.includes('⛈') || frameEvent.includes('⚠'))
  const pastStorm = stormTs != null && currentStep >= stormTs

  const bl = result?.baseline_at_deadline ?? null
  const ad = result?.adapted_at_deadline ?? null

  // Only show bug icons at/after the timestep the event occurred in the field
  // eventCells format: [row, col, event_type, severity, timestep]
  const visibleEventCells = eventCells.filter(ec => {
    const evTs = ec[4] ?? 0
    return currentStep >= evTs
  })

  return (
    <div style={{ flex: 1, overflow: 'hidden', display: 'flex', gap: 0 }}>

      {/* ── Left: Controls ───────────────────────────────────────────── */}
      <div style={{
        width: 210, background: 'var(--panel)', borderRight: '1px solid var(--border)',
        padding: 14, overflowY: 'auto', flexShrink: 0,
        display: 'flex', flexDirection: 'column', gap: 0,
      }}>
        <div className="section">
          <div className="section-title">Weather Event</div>
          <div className="toggle-row" style={{ marginBottom: 10 }}>
            <span className="toggle-label">Storm warning</span>
            <label className="toggle">
              <input type="checkbox" checked={weatherEnabled} onChange={e => setWeatherEnabled(e.target.checked)} />
              <span className="toggle-track" />
            </label>
          </div>
          {weatherEnabled && (
            <div className="field-group">
              <div className="field-label">
                <span>Time until storm</span>
                <span className="field-value">{weatherMinutes} min</span>
              </div>
              <input type="range" min={2} max={20} step={1} value={weatherMinutes}
                onChange={e => setWeatherMinutes(Number(e.target.value))} />
              <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 4 }}>
                Deadline: {weatherMinutes * 60}s per drone
              </div>
            </div>
          )}
        </div>

        <div className="section">
          <div className="section-title">Field Reports</div>
          <div className="toggle-row" style={{ marginBottom: 10 }}>
            <span className="toggle-label">Pest / sensor events</span>
            <label className="toggle">
              <input type="checkbox" checked={fieldEventsEnabled} onChange={e => setFieldEventsEnabled(e.target.checked)} />
              <span className="toggle-track" />
            </label>
          </div>
          {fieldEventsEnabled && (
            <div className="field-group">
              <div className="field-label">
                <span>Events during mission</span>
                <span className="field-value">{nFieldEvents}</span>
              </div>
              <input type="range" min={1} max={6} value={nFieldEvents}
                onChange={e => setNFieldEvents(Number(e.target.value))} />
            </div>
          )}
        </div>

        {!simResult && (
          <div style={{
            fontSize: 11, color: 'var(--text-dim)', padding: '7px 9px',
            background: 'rgba(255,255,255,0.04)', borderRadius: 4, marginBottom: 12, lineHeight: 1.5,
          }}>
            💡 Run <strong>Mission Baseline</strong> first to unlock before/after comparison.
          </div>
        )}

        {!apiKey && (
          <div style={{
            fontSize: 11, color: 'var(--warning)', padding: '7px 9px',
            background: 'rgba(210,153,34,0.1)', borderRadius: 4, marginBottom: 12,
          }}>
            API key required. Open ⚙ Settings.
          </div>
        )}

        <div style={{ marginTop: 'auto' }}>
          <button className="btn btn-primary" disabled={!field || running || !apiKey} onClick={handleRun}
            style={{ background: running ? undefined : '#5c2ef7' }}>
            {running ? '⟳ Claude is reasoning…' : '✦ Run with Claude'}
          </button>
          {done && <div style={{ fontSize: 11, color: 'var(--success)', textAlign: 'center', marginTop: 8 }}>✓ Adaptation complete</div>}
        </div>
      </div>

      {/* ── Middle: Decision Log (narrower) ───────────────────────────── */}
      <div style={{ width: 196, flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRight: '1px solid var(--border)' }}>
        <div style={{
          padding: '7px 12px', background: 'var(--panel)', borderBottom: '1px solid var(--border)',
          fontSize: 10, fontWeight: 600, color: 'var(--text-muted)',
          letterSpacing: '0.08em', textTransform: 'uppercase',
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          AI Decision Log
          {running && <span style={{ fontSize: 10, color: 'var(--accent)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>•</span>}
        </div>
        <div ref={logRef} style={{ flex: 1, overflowY: 'auto', padding: '8px 10px' }}>
          {log.length === 0 && !running && (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 8, color: 'var(--text-dim)', textAlign: 'center', padding: '0 8px' }}>
              <div style={{ fontSize: 24, opacity: 0.3 }}>✦</div>
              <div style={{ fontSize: 12 }}>
                {!apiKey ? 'Set your API key in ⚙ to enable AI' : 'Configure events and click Run'}
              </div>
            </div>
          )}
          {log.map((entry, i) => <LogEntry key={i} entry={entry} />)}
        </div>
      </div>

      {/* ── Right: Grid + Metrics + Outcome (expanded) ────────────────── */}
      <div style={{
        flex: 1, background: 'var(--panel)',
        display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0,
      }}>
        {/* Grid area — anchor canvas to top-left so overflow clips bottom, not top */}
        <div style={{
          flex: 1, display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
          background: '#08090f', overflow: 'hidden', position: 'relative', minHeight: 120,
          paddingTop: 6,
        }}>
          {frame ? (
            <GridMini grid={frame.grid} drones={frame.drones ?? []}
              dockPositions={config.dockPositions ?? [[0, 0]]}
              cellSize={cellSize} imageDataUrl={field?.image_data_url}
              mandatoryCells={mandatoryCells} eventCells={visibleEventCells} />
          ) : field ? (
            <GridMini grid={field.grid} dockPositions={config.dockPositions ?? [[0, 0]]}
              cellSize={cellSize} imageDataUrl={field?.image_data_url}
              mandatoryCells={[]} eventCells={[]} />
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>No field loaded</div>
          )}

          {/* Pre-storm event ticker (small banner at bottom) */}
          {frameEvent && !pastStorm && (
            <div style={{
              position: 'absolute', bottom: 4, left: 4, right: 4,
              padding: '4px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
              background: isStormEvent ? 'rgba(248,81,73,0.85)' : 'rgba(0,0,0,0.7)',
              color: '#fff', textAlign: 'center',
            }}>
              {frameEvent}
            </div>
          )}

          {/* Storm arrival — full overlay, mission ends here */}
          {pastStorm && (
            <div style={{
              position: 'absolute', inset: 0,
              background: 'rgba(8,9,15,0.88)',
              display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center',
              gap: 6,
            }}>
              <div style={{ fontSize: 44, lineHeight: 1 }}>⛈</div>
              <div style={{
                fontSize: 22, fontWeight: 800, color: '#f85149',
                letterSpacing: '0.03em', textAlign: 'center',
                lineHeight: 1.2,
              }}>
                Storm Arrived
              </div>
              <div style={{
                fontSize: 12, color: 'var(--text-muted)', textAlign: 'center',
                letterSpacing: '0.02em',
              }}>
                Drones recalled · Mission window closed
              </div>
              {(bl || ad) && (
                <div style={{
                  marginTop: 10,
                  background: 'rgba(255,255,255,0.05)',
                  border: '1px solid rgba(248,81,73,0.25)',
                  borderRadius: 8, padding: '10px 16px',
                  minWidth: 200,
                }}>
                  {/* Column headers */}
                  <div style={{ display: 'flex', gap: 0, marginBottom: 8 }}>
                    <div style={{ flex: 1 }} />
                    <div style={{ width: 70, textAlign: 'center', fontSize: 10, fontWeight: 600, color: '#6e7998', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Baseline</div>
                    <div style={{ width: 70, textAlign: 'center', fontSize: 10, fontWeight: 600, color: '#9c6fdb', textTransform: 'uppercase', letterSpacing: '0.05em' }}>AI Plan</div>
                  </div>
                  {/* Overall coverage row */}
                  {(bl?.coverage_pct != null || ad?.coverage_pct != null) && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 6 }}>
                      <div style={{ flex: 1, fontSize: 10, color: 'var(--text-dim)' }}>Coverage</div>
                      <div style={{ width: 70, textAlign: 'center', fontSize: 18, fontWeight: 700, color: '#6e7998' }}>
                        {bl?.coverage_pct != null ? `${bl.coverage_pct.toFixed(1)}%` : '—'}
                      </div>
                      <div style={{ width: 70, textAlign: 'center', fontSize: 18, fontWeight: 700, color: '#9c6fdb' }}>
                        {ad?.coverage_pct != null ? `${ad.coverage_pct.toFixed(1)}%` : '—'}
                      </div>
                    </div>
                  )}
                  {/* Priority zones row */}
                  {(bl?.top_priority_coverage != null || ad?.top_priority_coverage != null) && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 0, paddingTop: 6, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                      <div style={{ flex: 1, fontSize: 10, color: 'var(--text-dim)' }}>Priority ★</div>
                      <div style={{ width: 70, textAlign: 'center', fontSize: 18, fontWeight: 700, color: '#6e7998' }}>
                        {bl?.top_priority_coverage != null ? `${(bl.top_priority_coverage * 100).toFixed(1)}%` : '—'}
                      </div>
                      <div style={{ width: 70, textAlign: 'center', fontSize: 18, fontWeight: 700,
                        color: (ad?.top_priority_coverage ?? 0) > (bl?.top_priority_coverage ?? 0) ? 'var(--success)' : '#9c6fdb',
                      }}>
                        {ad?.top_priority_coverage != null ? `${(ad.top_priority_coverage * 100).toFixed(1)}%` : '—'}
                      </div>
                    </div>
                  )}
                </div>
              )}
              <button
                className="btn btn-secondary"
                style={{ marginTop: 8, fontSize: 11 }}
                onClick={() => { setCurrentStep(0); setPlaying(true) }}
              >
                ↩ Replay
              </button>
            </div>
          )}
        </div>

        {/* Player */}
        {history && (
          <div style={{
            padding: '5px 12px', background: 'var(--panel)',
            borderTop: '1px solid var(--border)',
          }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <button className="btn btn-secondary btn-icon" style={{ fontSize: 11 }}
                onClick={() => {
                  if (pastStorm || currentStep >= (stormTs ?? totalSteps - 1)) {
                    setCurrentStep(0); setPlaying(true)
                  } else {
                    setPlaying(!playing)
                  }
                }}>
                {pastStorm ? '↩' : playing ? '⏸' : '▶'}
              </button>
              <input type="range" min={0} max={Math.max(0, stormTs ?? totalSteps - 1)} value={currentStep}
                style={{ flex: 1 }}
                onChange={e => { setCurrentStep(Number(e.target.value)); setPlaying(false) }} />
              <span style={{ fontSize: 10, color: pastStorm ? 'var(--danger)' : 'var(--text-dim)', minWidth: 48, textAlign: 'right', fontWeight: pastStorm ? 600 : 400 }}>
                {pastStorm ? '⛈' : ''} {currentStep}/{stormTs ?? totalSteps - 1}
              </span>
            </div>
            {stormTs != null && !pastStorm && (
              <div style={{ fontSize: 10, textAlign: 'center', color: 'var(--text-dim)', marginTop: 2 }}>
                ⛈ Storm at t={stormTs} · {stormTs - currentStep} steps remaining
              </div>
            )}
          </div>
        )}

        {/* Grouped bar chart metrics */}
        <MetricsChart bl={bl} ad={ad} />

        {/* Field events table (collapsible) — shows all events, highlights revealed ones */}
        <EventsTable
          eventCells={eventCells}
          visibleCount={visibleEventCells.length}
          currentStep={currentStep}
          grid={frame?.grid ?? field?.grid ?? null}
        />

        {/* Outcome report — markdown rendered */}
        {summary && (
          <div style={{
            padding: '10px 14px', borderTop: '1px solid var(--border)',
            background: 'rgba(92,46,247,0.06)', overflowY: 'auto', maxHeight: 150,
          }}>
            <div style={{
              fontSize: 10, fontWeight: 600, color: '#9c6fdb',
              textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
            }}>
              ✦ Outcome Report
            </div>
            <SimpleMarkdown
              text={summary}
              style={{ fontSize: 11, color: 'var(--text)', lineHeight: 1.65 }}
            />
          </div>
        )}
      </div>
    </div>
  )
}
