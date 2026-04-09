import { useState, useRef, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Legend,
} from 'recharts'
import { streamMonteCarlo } from '../api.js'

// ── Histogram helper ──────────────────────────────────────────────────────────

function makeHistogram(values, nBins = 15) {
  if (!values.length) return []
  const min = Math.floor(Math.min(...values))
  const max = Math.ceil(Math.max(...values))
  const range = max - min || 1
  const binWidth = range / nBins

  const bins = Array.from({ length: nBins }, (_, i) => ({
    x: +(min + i * binWidth).toFixed(1),
    label: `${(min + i * binWidth).toFixed(0)}`,
    count: 0,
  }))

  for (const v of values) {
    const idx = Math.min(Math.floor((v - min) / binWidth), nBins - 1)
    bins[idx].count++
  }
  return bins
}

// ── Config column ─────────────────────────────────────────────────────────────

function ConfigCol({ title, nDrones, seedSpacingM, windSpeed, nRuns,
                     onDrones, onSeedSpacing, onWindSpeed, onRuns }) {
  return (
    <div className="mc-config-col">
      <div className="mc-config-col-title">{title}</div>

      <div className="field-group">
        <div className="field-label">
          <span>Drones</span>
          <span className="field-value">{nDrones}</span>
        </div>
        <input type="range" min={1} max={8} value={nDrones}
          onChange={(e) => onDrones(Number(e.target.value))} />
      </div>

      <div className="field-group">
        <div className="field-label">
          <span>Seed spacing</span>
          <span className="field-value">{seedSpacingM.toFixed(1)} m</span>
        </div>
        <input type="range" min={0.5} max={3.0} step={0.1} value={seedSpacingM}
          onChange={(e) => onSeedSpacing(Number(e.target.value))} />
      </div>

      <div className="field-group">
        <div className="field-label">
          <span>Wind speed</span>
          <span className="field-value">{windSpeed === 0 ? 'calm' : `${windSpeed} m/s`}</span>
        </div>
        <input type="range" min={0} max={15} step={1} value={windSpeed}
          onChange={(e) => onWindSpeed(Number(e.target.value))} />
      </div>

      <div className="field-group">
        <div className="field-label">
          <span>Simulations</span>
          <span className="field-value">{nRuns}</span>
        </div>
        <div className="seg-control">
          {[20, 50, 100].map((n) => (
            <button key={n} className={`seg-btn ${nRuns === n ? 'active' : ''}`}
              onClick={() => onRuns(n)}>
              {n}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Stats row ─────────────────────────────────────────────────────────────────

function StatsRow({ result, label, color }) {
  if (!result) return null
  return (
    <div className="mc-stats">
      <div className="mc-stat">
        <div className={`mc-stat-val p5`} style={{ color: 'var(--danger)' }}>
          {result.coverage_p5?.toFixed(1)}%
        </div>
        <div className="mc-stat-label">P5 (worst)</div>
      </div>
      <div className="mc-stat">
        <div className="mc-stat-val p50">{result.coverage_p50?.toFixed(1)}%</div>
        <div className="mc-stat-label">Median</div>
      </div>
      <div className="mc-stat">
        <div className="mc-stat-val p95" style={{ color: 'var(--success)' }}>
          {result.coverage_p95?.toFixed(1)}%
        </div>
        <div className="mc-stat-label">P95 (best)</div>
      </div>
      <div className="mc-stat">
        <div className="mc-stat-val neutral" style={{ color: 'var(--text)' }}>
          ±{result.coverage_std?.toFixed(1)}%
        </div>
        <div className="mc-stat-label">Std dev</div>
      </div>
    </div>
  )
}

// ── Custom tooltip ────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: 'var(--panel2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '8px 12px', fontSize: 12,
    }}>
      <div style={{ color: 'var(--text-muted)' }}>{label}%</div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ color: p.color }}>
          {p.name}: {p.value} runs
        </div>
      ))}
    </div>
  )
}

// ── Main MCView ───────────────────────────────────────────────────────────────

export default function MCView({ field, config, onConfigChange }) {
  const [configA, setConfigA] = useState({
    nDrones: config.nDrones ?? 3,
    seedSpacingM: config.seedSpacingM ?? 1.5,
    windSpeed: 0,
    nRuns: 50,
  })
  const [configB, setConfigB] = useState({
    nDrones: Math.min(8, (config.nDrones ?? 3) + 2),
    seedSpacingM: Math.max(0.5, (config.seedSpacingM ?? 1.5) - 0.5),
    windSpeed: 0,
    nRuns: 50,
  })

  const [progressA, setProgressA] = useState(null)  // { done, total }
  const [progressB, setProgressB] = useState(null)
  const [resultA, setResultA] = useState(null)
  const [resultB, setResultB] = useState(null)
  const [runningA, setRunningA] = useState(false)
  const [runningB, setRunningB] = useState(false)
  const [error, setError] = useState(null)

  const cleanupA = useRef(null)
  const cleanupB = useRef(null)

  const runAnalysis = useCallback((which) => {
    if (!field) return
    const cfg = which === 'A' ? configA : configB
    const setRunning = which === 'A' ? setRunningA : setRunningB
    const setProgress = which === 'A' ? setProgressA : setProgressB
    const setResult = which === 'A' ? setResultA : setResultB
    const cleanupRef = which === 'A' ? cleanupA : cleanupB

    // Cancel existing run
    cleanupRef.current?.()
    setRunning(true)
    setProgress({ done: 0, total: cfg.nRuns })
    setResult(null)
    setError(null)

    const cancel = streamMonteCarlo(
      {
        grid: field.grid,
        soil_mask: field.soil_mask ?? null,
        nrows: field.nrows,
        ncols: field.ncols,
        n_drones: cfg.nDrones,
        dock_positions: config.dockPositions ?? [[0, 0]],
        orientation_deg: config.orientationDeg ?? 0,
        strip_mode: config.stripMode ?? 'lawnmower',
        strip_width: config.stripWidth ?? 2,
        n_runs: cfg.nRuns,
        seed_spacing_m: cfg.seedSpacingM,
        wind_speed_ms: cfg.windSpeed,
        battery_life_minutes: config.batteryLifeMin ?? 35,
        recharge_time_minutes: config.rechargeTimeMin ?? 60,
        seed_capacity: config.seedCapacity ?? 6000,
        seed_jitter_sigma: config.seedJitter ?? 0.3,
        failure_prob: config.failureProb ?? 0,
        survival_rate: config.survivalRate ?? 0.4,
        seconds_per_cell: config.secondsPerCell ?? 2.0,
        seed: 42,
      },
      {
        onProgress: (d) => setProgress({ done: d.done, total: d.total }),
        onResult: (d) => { setResult(d); setRunning(false); setProgress(null) },
        onError: (e) => { setError(e.message); setRunning(false); setProgress(null) },
      }
    )
    cleanupRef.current = cancel
  }, [field, configA, configB, config])

  // Build combined histogram data
  const histData = (() => {
    if (!resultA && !resultB) return []
    const allVals = [
      ...(resultA?.all_coverage ?? []),
      ...(resultB?.all_coverage ?? []),
    ]
    if (!allVals.length) return []
    const min = Math.floor(Math.min(...allVals))
    const max = Math.ceil(Math.max(...allVals))
    const nBins = 15
    const binWidth = (max - min || 1) / nBins

    return Array.from({ length: nBins }, (_, i) => {
      const lo = min + i * binWidth
      const hi = lo + binWidth
      const label = `${lo.toFixed(0)}`
      const countA = resultA
        ? resultA.all_coverage.filter((v) => v >= lo && v < hi).length
        : undefined
      const countB = resultB
        ? resultB.all_coverage.filter((v) => v >= lo && v < hi).length
        : undefined
      return { label, countA, countB }
    })
  })()

  const hasResults = resultA || resultB

  return (
    <div className="mc-area">
      {/* Config row */}
      <div className="mc-config-row">
        <ConfigCol
          title={`Config A — ${configA.nDrones} drones`}
          nDrones={configA.nDrones}
          seedSpacingM={configA.seedSpacingM}
          windSpeed={configA.windSpeed}
          nRuns={configA.nRuns}
          onDrones={(v) => setConfigA((c) => ({ ...c, nDrones: v }))}
          onSeedSpacing={(v) => setConfigA((c) => ({ ...c, seedSpacingM: v }))}
          onWindSpeed={(v) => setConfigA((c) => ({ ...c, windSpeed: v }))}
          onRuns={(v) => setConfigA((c) => ({ ...c, nRuns: v }))}
        />
        <ConfigCol
          title={`Config B — ${configB.nDrones} drones`}
          nDrones={configB.nDrones}
          seedSpacingM={configB.seedSpacingM}
          windSpeed={configB.windSpeed}
          nRuns={configB.nRuns}
          onDrones={(v) => setConfigB((c) => ({ ...c, nDrones: v }))}
          onSeedSpacing={(v) => setConfigB((c) => ({ ...c, seedSpacingM: v }))}
          onWindSpeed={(v) => setConfigB((c) => ({ ...c, windSpeed: v }))}
          onRuns={(v) => setConfigB((c) => ({ ...c, nRuns: v }))}
        />
      </div>

      {/* Run buttons */}
      <div style={{ display: 'flex', gap: 10 }}>
        <div style={{ flex: 1 }}>
          {progressA && (
            <div style={{ marginBottom: 6 }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                Running {progressA.done} / {progressA.total}…
              </div>
              <div className="progress-bar">
                <div className="progress-fill"
                  style={{ width: `${(progressA.done / progressA.total) * 100}%` }} />
              </div>
            </div>
          )}
          <button
            className="btn btn-primary"
            disabled={!field || runningA}
            onClick={() => runAnalysis('A')}
          >
            {runningA ? '⟳ Running A…' : '▶ Run Config A'}
          </button>
        </div>
        <div style={{ flex: 1 }}>
          {progressB && (
            <div style={{ marginBottom: 6 }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                Running {progressB.done} / {progressB.total}…
              </div>
              <div className="progress-bar">
                <div className="progress-fill"
                  style={{ width: `${(progressB.done / progressB.total) * 100}%` }} />
              </div>
            </div>
          )}
          <button
            className="btn btn-primary"
            style={{ background: '#7c4ef7' }}
            disabled={!field || runningB}
            onClick={() => runAnalysis('B')}
          >
            {runningB ? '⟳ Running B…' : '▶ Run Config B'}
          </button>
        </div>
      </div>

      {error && (
        <div style={{
          fontSize: 12, color: 'var(--danger)', padding: '8px 12px',
          background: 'rgba(248,81,73,0.1)', borderRadius: 6,
        }}>
          {error}
        </div>
      )}

      {!hasResults && !runningA && !runningB && (
        <div className="mc-empty">
          <div style={{ fontSize: 28, opacity: 0.3 }}>📊</div>
          <div>Run an analysis to see reliability distributions</div>
          <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            Compare two fleet configs: drones, seed spacing, wind conditions
          </div>
        </div>
      )}

      {hasResults && (
        <>
          {/* Coverage histogram */}
          <div className="mc-chart-card">
            <div className="mc-chart-title">Seeding Distribution (% plantable cells covered)</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={histData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                  tickLine={false} axisLine={{ stroke: 'var(--border)' }} />
                <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                  tickLine={false} axisLine={false} />
                <Tooltip content={<CustomTooltip />} />
                {resultA && <Bar dataKey="countA" name={`A (${configA.nDrones}d)`}
                  fill="#4f8ef7" fillOpacity={0.8} radius={[2, 2, 0, 0]} />}
                {resultB && <Bar dataKey="countB" name={`B (${configB.nDrones}d)`}
                  fill="#7c4ef7" fillOpacity={0.8} radius={[2, 2, 0, 0]} />}
                {resultA && <ReferenceLine x={resultA.coverage_p5?.toFixed(0)}
                  stroke="var(--danger)" strokeDasharray="4 2"
                  label={{ value: 'P5', fill: 'var(--danger)', fontSize: 9 }} />}
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Stats */}
          <div className="mc-results-row">
            {resultA && (
              <div className="mc-chart-card">
                <div className="mc-chart-title" style={{ color: '#4f8ef7' }}>
                  Config A — {configA.nDrones} drones · {configA.seedSpacingM.toFixed(1)} m spacing
                </div>
                <StatsRow result={resultA} />
                {resultA.seeds_median != null && (
                  <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-muted)' }}>
                    Seeds (median): <strong>{Math.round(resultA.seeds_median).toLocaleString()}</strong>
                    {resultA.survivors_median != null && (
                      <>{' '}· Survivors: <strong>{Math.round(resultA.survivors_median).toLocaleString()}</strong></>
                    )}
                  </div>
                )}
              </div>
            )}
            {resultB && (
              <div className="mc-chart-card">
                <div className="mc-chart-title" style={{ color: '#7c4ef7' }}>
                  Config B — {configB.nDrones} drones · {configB.seedSpacingM.toFixed(1)} m spacing
                </div>
                <StatsRow result={resultB} />
                {resultB.seeds_median != null && (
                  <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-muted)' }}>
                    Seeds (median): <strong>{Math.round(resultB.seeds_median).toLocaleString()}</strong>
                    {resultB.survivors_median != null && (
                      <>{' '}· Survivors: <strong>{Math.round(resultB.survivors_median).toLocaleString()}</strong></>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Takeaway */}
          {resultA && resultB && (
            <div style={{
              background: 'var(--panel)', border: '1px solid var(--border)',
              borderRadius: 8, padding: '12px 16px', fontSize: 12,
              lineHeight: 1.6, color: 'var(--text-muted)',
            }}>
              <strong style={{ color: 'var(--text)' }}>Takeaway: </strong>
              {(() => {
                const deltaP5 = resultB.coverage_p5 - resultA.coverage_p5
                const deltaMedian = resultB.coverage_p50 - resultA.coverage_p50
                const dronesDelta = configB.nDrones - configA.nDrones
                const spacingDelta = configA.seedSpacingM - configB.seedSpacingM
                if (Math.abs(deltaP5) < 2 && Math.abs(deltaMedian) < 2) {
                  const droneStr = dronesDelta !== 0
                    ? `Adding ${Math.abs(dronesDelta)} drone${Math.abs(dronesDelta) > 1 ? 's' : ''}`
                    : 'Config B'
                  return `${droneStr} has minimal impact on coverage under these conditions.`
                }
                const dir = deltaP5 > 0 ? 'improves' : 'reduces'
                const note = spacingDelta > 0.1 ? ` (tighter spacing: ${configB.seedSpacingM.toFixed(1)} m)` : ''
                return `Config B ${dir} worst-case seeding by ${Math.abs(deltaP5).toFixed(1)}pp (P5)${note}. ` +
                  `Median shifts ${deltaMedian > 0 ? '+' : ''}${deltaMedian.toFixed(1)}pp.`
              })()}
            </div>
          )}
        </>
      )}
    </div>
  )
}
