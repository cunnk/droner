import { useState, useRef, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
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

function ConfigCol({ title, nDrones, seedSpacingM, windSpeed, failureProb, seedCapacity, nRuns,
                     onDrones, onSeedSpacing, onWindSpeed, onFailureProb, onSeedCapacity, onRuns }) {
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
          <span>Seed capacity</span>
          <span className="field-value">{seedCapacity.toLocaleString()} seeds</span>
        </div>
        <input type="range" min={1000} max={12000} step={500} value={seedCapacity}
          onChange={(e) => onSeedCapacity(Number(e.target.value))} />
      </div>

      <div className="field-group">
        <div className="field-label">
          <span>Drone failure prob</span>
          <span className="field-value" style={{ color: failureProb > 0 ? 'var(--danger)' : 'var(--text-muted)' }}>
            {failureProb === 0 ? 'none' : `${Math.round(failureProb * 100)}%`}
          </span>
        </div>
        <input type="range" min={0} max={0.5} step={0.05} value={failureProb}
          onChange={(e) => onFailureProb(Number(e.target.value))} />
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

function StatBox({ val, label, color }) {
  return (
    <div className="mc-stat">
      <div className="mc-stat-val" style={color ? { color } : undefined}>{val}</div>
      <div className="mc-stat-label">{label}</div>
    </div>
  )
}

function SectionLabel({ children }) {
  return (
    <div style={{
      fontSize: 10, fontWeight: 600, letterSpacing: '0.08em',
      textTransform: 'uppercase', color: 'var(--text-dim)',
      marginTop: 10, marginBottom: 4,
    }}>
      {children}
    </div>
  )
}

function StatsRow({ result }) {
  if (!result) return null

  const distKm = result.distance_median_m != null
    ? (result.distance_median_m / 1000).toFixed(1)
    : null

  return (
    <div>
      {/* Primary: makespan */}
      <SectionLabel>Mission Duration</SectionLabel>
      <div className="mc-stats">
        {result.makespan_p5 != null && (
          <StatBox val={`${Math.round(result.makespan_p5)}`} label="P5 best-case" color="var(--success)" />
        )}
        {result.makespan_median != null && (
          <StatBox val={`${Math.round(result.makespan_median)}`} label="Median steps" />
        )}
        {result.makespan_p95 != null && (
          <StatBox val={`${Math.round(result.makespan_p95)}`} label="P95 worst-case" color="var(--danger)" />
        )}
        {result.recharges_median != null && (
          <StatBox val={result.recharges_median.toFixed(1)} label="Recharges (med)" />
        )}
        {distKm != null && (
          <StatBox val={`${distKm} km`} label="Fleet dist (med)" />
        )}
      </div>

      {/* Secondary: seeds */}
      <SectionLabel>Seeds</SectionLabel>
      <div className="mc-stats" style={{ flexWrap: 'wrap' }}>
        {result.seeds_median != null && (
          <StatBox val={Math.round(result.seeds_median).toLocaleString()} label="Seeds (med)" />
        )}
        {result.survivors_median != null && (
          <StatBox val={Math.round(result.survivors_median).toLocaleString()} label="Survivors (med)" color="var(--success)" />
        )}
        {result.refills_median != null && (
          <StatBox val={result.refills_median.toFixed(1)} label="Seed refills" />
        )}
      </div>

      {/* Tertiary: coverage (deemphasised) */}
      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-dim)', display: 'flex', gap: 16 }}>
        <span>Coverage — P5: <strong style={{ color: 'var(--text-muted)' }}>{result.coverage_p5?.toFixed(1)}%</strong></span>
        <span>Median: <strong style={{ color: 'var(--text-muted)' }}>{result.coverage_p50?.toFixed(1)}%</strong></span>
        <span>P95: <strong style={{ color: 'var(--text-muted)' }}>{result.coverage_p95?.toFixed(1)}%</strong></span>
        <span>±{result.coverage_std?.toFixed(1)}%</span>
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
      <div style={{ color: 'var(--text-muted)' }}>{label} steps</div>
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
    failureProb: 0,
    seedCapacity: config.seedCapacity ?? 6000,
    nRuns: 50,
  })
  const [configB, setConfigB] = useState({
    nDrones: Math.min(8, (config.nDrones ?? 3) + 2),
    seedSpacingM: Math.max(0.5, (config.seedSpacingM ?? 1.5) - 0.5),
    windSpeed: 0,
    failureProb: 0.15,
    seedCapacity: config.seedCapacity ?? 6000,
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
        seed_capacity: cfg.seedCapacity ?? 6000,
        seed_jitter_sigma: config.seedJitter ?? 0.3,
        failure_prob: cfg.failureProb ?? 0,
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

  // Build combined makespan histogram
  const histData = (() => {
    if (!resultA && !resultB) return []
    const allVals = [
      ...(resultA?.all_makespan_raw ?? []),
      ...(resultB?.all_makespan_raw ?? []),
    ]
    if (!allVals.length) return []

    const globalMin = Math.floor(Math.min(...allVals))
    const globalMax = Math.ceil(Math.max(...allVals))
    const range = globalMax - globalMin || 1

    // Use integer bin widths so every label is a unique integer string —
    // duplicate labels in a categorical BarChart cause bars to overwrite each other.
    const binWidth = Math.max(1, Math.ceil(range / 15))
    const nBins = Math.ceil(range / binWidth) + 1   // +1 ensures globalMax is covered

    return Array.from({ length: nBins }, (_, i) => {
      const lo = globalMin + i * binWidth
      const hi = lo + binWidth
      const isLast = i === nBins - 1
      // Include hi on the last bin so globalMax values aren't silently dropped
      const inBin = (v) => v >= lo && (isLast ? v <= hi : v < hi)

      return {
        label: String(lo),
        countA: resultA?.all_makespan_raw != null
          ? resultA.all_makespan_raw.filter(inBin).length
          : undefined,
        countB: resultB?.all_makespan_raw != null
          ? resultB.all_makespan_raw.filter(inBin).length
          : undefined,
      }
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
          failureProb={configA.failureProb}
          seedCapacity={configA.seedCapacity}
          nRuns={configA.nRuns}
          onDrones={(v) => setConfigA((c) => ({ ...c, nDrones: v }))}
          onSeedSpacing={(v) => setConfigA((c) => ({ ...c, seedSpacingM: v }))}
          onWindSpeed={(v) => setConfigA((c) => ({ ...c, windSpeed: v }))}
          onFailureProb={(v) => setConfigA((c) => ({ ...c, failureProb: v }))}
          onSeedCapacity={(v) => setConfigA((c) => ({ ...c, seedCapacity: v }))}
          onRuns={(v) => setConfigA((c) => ({ ...c, nRuns: v }))}
        />
        <ConfigCol
          title={`Config B — ${configB.nDrones} drones`}
          nDrones={configB.nDrones}
          seedSpacingM={configB.seedSpacingM}
          windSpeed={configB.windSpeed}
          failureProb={configB.failureProb}
          seedCapacity={configB.seedCapacity}
          nRuns={configB.nRuns}
          onDrones={(v) => setConfigB((c) => ({ ...c, nDrones: v }))}
          onSeedSpacing={(v) => setConfigB((c) => ({ ...c, seedSpacingM: v }))}
          onWindSpeed={(v) => setConfigB((c) => ({ ...c, windSpeed: v }))}
          onFailureProb={(v) => setConfigB((c) => ({ ...c, failureProb: v }))}
          onSeedCapacity={(v) => setConfigB((c) => ({ ...c, seedCapacity: v }))}
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
          {/* Makespan histogram */}
          <div className="mc-chart-card">
            <div className="mc-chart-title">Mission Duration Distribution (steps to complete)</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={histData} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                  tickLine={false} axisLine={{ stroke: 'var(--border)' }}
                  label={{ value: 'steps', position: 'insideBottomRight', offset: -4,
                           fill: 'var(--text-dim)', fontSize: 9 }} />
                <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10 }}
                  tickLine={false} axisLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {resultA && <Bar dataKey="countA" name={`Config A · ${configA.nDrones}d · med ${Math.round(resultA.makespan_median ?? 0)} steps`}
                  fill="#4f8ef7" fillOpacity={0.8} radius={[2, 2, 0, 0]} />}
                {resultB && <Bar dataKey="countB" name={`Config B · ${configB.nDrones}d · med ${Math.round(resultB.makespan_median ?? 0)} steps`}
                  fill="#7c4ef7" fillOpacity={0.8} radius={[2, 2, 0, 0]} />}
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Stats */}
          <div className="mc-results-row">
            {resultA && (
              <div className="mc-chart-card">
                <div className="mc-chart-title" style={{ color: '#4f8ef7' }}>
                  Config A — {configA.nDrones} drones · {configA.seedSpacingM.toFixed(1)} m spacing
                  {configA.failureProb > 0 && (
                    <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--danger)' }}>
                      {Math.round(configA.failureProb * 100)}% fail prob
                    </span>
                  )}
                </div>
                <StatsRow result={resultA} />
              </div>
            )}
            {resultB && (
              <div className="mc-chart-card">
                <div className="mc-chart-title" style={{ color: '#7c4ef7' }}>
                  Config B — {configB.nDrones} drones · {configB.seedSpacingM.toFixed(1)} m spacing
                  {configB.failureProb > 0 && (
                    <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--danger)' }}>
                      {Math.round(configB.failureProb * 100)}% fail prob
                    </span>
                  )}
                </div>
                <StatsRow result={resultB} />
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
                const mA = resultA.makespan_median ?? 0
                const mB = resultB.makespan_median ?? 0
                const mDelta = mB - mA
                const mPct = mA > 0 ? ((mDelta / mA) * 100).toFixed(0) : null

                const dronesDelta = configB.nDrones - configA.nDrones
                const failDelta = configB.failureProb - configA.failureProb
                const spacingDelta = configA.seedSpacingM - configB.seedSpacingM

                const conditions = []
                if (dronesDelta !== 0) conditions.push(
                  `${Math.abs(dronesDelta)} ${dronesDelta > 0 ? 'more' : 'fewer'} drone${Math.abs(dronesDelta) > 1 ? 's' : ''}`
                )
                if (failDelta > 0.01) conditions.push(`+${Math.round(failDelta * 100)}pp failure risk`)
                else if (failDelta < -0.01) conditions.push(`${Math.round(Math.abs(failDelta) * 100)}pp less failure risk`)
                if (spacingDelta > 0.1) conditions.push(`tighter spacing`)

                const condStr = conditions.length ? ` with ${conditions.join(', ')}` : ''

                if (Math.abs(mDelta) < 2) {
                  return `Config B${condStr} shows no meaningful difference in mission duration (median ${Math.round(mB)} vs ${Math.round(mA)} steps).`
                }

                const dir = mDelta < 0 ? 'cuts' : 'adds'
                const pctStr = mPct ? ` (${Math.abs(mPct)}%)` : ''
                const worstA = Math.round(resultA.makespan_p95 ?? 0)
                const worstB = Math.round(resultB.makespan_p95 ?? 0)
                const worstDelta = worstB - worstA
                return `Config B${condStr} ${dir} median mission time by ${Math.abs(Math.round(mDelta))} steps${pctStr} ` +
                  `(${Math.round(mA)} → ${Math.round(mB)}). ` +
                  `Worst-case shifts ${worstDelta > 0 ? '+' : ''}${worstDelta} steps (P95: ${worstA} → ${worstB}).`
              })()}
            </div>
          )}
        </>
      )}
    </div>
  )
}
