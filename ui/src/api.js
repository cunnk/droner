const BASE = '/api'

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function uploadField(file, params) {
  const form = new FormData()
  form.append('file', file)
  const url = new URL(`${BASE}/field/upload`, window.location.href)
  // Pass all params (including soil detection thresholds) as query params
  Object.entries(params).forEach(([k, v]) => {
    if (v != null) url.searchParams.set(k, String(v))
  })
  const res = await fetch(url.toString(), { method: 'POST', body: form })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function runSimulation(params) {
  return post('/simulate', params)
}

/**
 * Stream Monte Carlo analysis via SSE.
 * Returns a cleanup function that aborts the request.
 * onProgress({ done, total }), onResult(resultData), onError(err)
 */
export function streamMonteCarlo(params, { onProgress, onResult, onError }) {
  const controller = new AbortController()

  fetch(`${BASE}/monte-carlo/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) throw new Error(await res.text())
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'progress') onProgress?.(data)
            if (data.type === 'result') onResult?.(data)
          } catch {}
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError?.(err)
    })

  return () => controller.abort()
}
