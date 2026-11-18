async function req(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  return res.json()
}

export const api = {
  health: () => req('/api/health'),
  overview: () => req('/api/overview'),
  waybills: () => req('/api/waybills'),
  timeline: (id) => req(`/api/waybills/${encodeURIComponent(id)}/timeline`),
  quarantine: () => req('/api/quarantine'),
  sidecar: () => req('/api/sidecar'),
  ingest: () => req('/api/ingest/run', { method: 'POST' }),
  proofs: (waybillId) =>
    req(waybillId ? `/api/status-proofs?waybill_id=${encodeURIComponent(waybillId)}` : '/api/status-proofs'),

  // 异常轴分流（exception_axis_split）
  exceptionSet: () => req('/api/exception-set'),
  saveExceptionSet: (codes, note) =>
    req('/api/exception-set', {
      method: 'PUT',
      body: JSON.stringify({ codes, note }),
    }),
  exceptionHops: (waybillId) =>
    req(waybillId ? `/api/exception-hops?waybill_id=${encodeURIComponent(waybillId)}` : '/api/exception-hops'),
  axisProofs: (waybillId) =>
    req(waybillId ? `/api/axis-proofs?waybill_id=${encodeURIComponent(waybillId)}` : '/api/axis-proofs'),
  waybillAxisProof: (id) => req(`/api/waybills/${encodeURIComponent(id)}/axis-proof`),
}
