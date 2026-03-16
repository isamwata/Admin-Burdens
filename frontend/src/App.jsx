import { useEffect, useRef, useState } from 'react'

// In production (HF Spaces / Docker) the frontend is served by the same
// FastAPI server, so API calls use the same origin (empty string).
// For local dev: VITE_API_URL=http://localhost:8002 npm run dev
const API = import.meta.env.VITE_API_URL || ''

// ── helpers ──────────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
  const map = {
    queued:    { label: 'Queued',    color: '#718096' },
    scraping:  { label: 'Scraping',  color: '#d97706' },
    running:   { label: 'Running',   color: '#d97706' },
    ingesting: { label: 'Indexing',  color: '#d97706' },
    done:      { label: 'Done',      color: '#276749' },
    error:     { label: 'Error',     color: '#c53030' },
  }
  const s = map[status] || { label: status, color: '#718096' }
  return (
    <span style={{ background: s.color, color: '#fff', borderRadius: 4,
                   padding: '2px 8px', fontSize: 12, fontWeight: 600 }}>
      {s.label}
    </span>
  )
}

function ProgressBar({ value }) {
  return (
    <div style={{ background: '#e2e8f0', borderRadius: 4, height: 8, margin: '8px 0' }}>
      <div style={{
        width: `${value}%`, background: '#2b6cb0',
        borderRadius: 4, height: '100%', transition: 'width .4s ease',
      }} />
    </div>
  )
}

// ── main component ────────────────────────────────────────────────────────────
export default function App() {
  const [docTypes, setDocTypes] = useState(['Koninklijk besluit'])
  const [form, setForm] = useState({
    start_date: '',
    end_date: '',
    doc_type: 'Koninklijk besluit',
  })

  // scrape state
  const [scrapeJob, setScrapeJob] = useState(null)   // { id, status, progress, progress_text, count, error }
  const [preview, setPreview]     = useState([])
  const [total, setTotal]         = useState(0)

  // predict state
  const [predictJob, setPredictJob] = useState(null) // { id, status, error }

  // ingest state (auto-triggered after scrape)
  const [ingestJob, setIngestJob]   = useState(null) // { id, status, chunks_stored, db_total, message, error }

  const scrapeTimer   = useRef(null)
  const predictTimer  = useRef(null)
  const ingestTimer   = useRef(null)

  // Fetch available document types from backend
  useEffect(() => {
    fetch(`${API}/api/document-types`)
      .then(r => r.json())
      .then(d => setDocTypes(d.types))
      .catch(() => {})
  }, [])

  // ── scraping ────────────────────────────────────────────────────────────────
  async function handleScrape() {
    clearInterval(scrapeTimer.current)
    clearInterval(ingestTimer.current)
    setPredictJob(null)
    setIngestJob(null)
    setPreview([])
    setTotal(0)

    const res = await fetch(`${API}/api/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start_date: form.start_date,
        end_date: form.end_date,
        doc_types: [form.doc_type],
      }),
    })
    const data = await res.json()
    if (!res.ok || !data.job_id) {
      setScrapeJob({ id: null, status: 'error', error: data.detail || JSON.stringify(data) })
      return
    }
    const { job_id } = data
    setScrapeJob({ id: job_id, status: 'queued', progress: 0, progress_text: '' })

    scrapeTimer.current = setInterval(async () => {
      const s = await fetch(`${API}/api/jobs/${job_id}`).then(r => r.json())
      setScrapeJob(prev => ({ ...prev, ...s, id: job_id }))

      if (s.status === 'done') {
        clearInterval(scrapeTimer.current)
        const p = await fetch(`${API}/api/jobs/${job_id}/preview`).then(r => r.json())
        setPreview(p.data)
        setTotal(p.total)

        // Auto-poll the predict+store job triggered by the backend
        if (s.predict_job_id) {
          setPredictJob({ id: s.predict_job_id, status: 'queued', progress_text: 'Starting…' })
          predictTimer.current = setInterval(async () => {
            const pj = await fetch(`${API}/api/jobs/${s.predict_job_id}`).then(r => r.json())
            setPredictJob(prev => ({ ...prev, ...pj, id: s.predict_job_id }))
            if (pj.status === 'done' || pj.status === 'error') {
              clearInterval(predictTimer.current)
            }
          }, 2000)
        }
      }
      if (s.status === 'error') {
        clearInterval(scrapeTimer.current)
      }
    }, 2000)
  }

  // ── predictions ─────────────────────────────────────────────────────────────
  async function handlePredict() {
    clearInterval(predictTimer.current)

    const res = await fetch(`${API}/api/predict/${scrapeJob.id}`, { method: 'POST' })
    const { job_id } = await res.json()
    setPredictJob({ id: job_id, status: 'queued' })

    predictTimer.current = setInterval(async () => {
      const s = await fetch(`${API}/api/jobs/${job_id}`).then(r => r.json())
      setPredictJob(prev => ({ ...prev, ...s, id: job_id }))
      if (s.status === 'done' || s.status === 'error') {
        clearInterval(predictTimer.current)
      }
    }, 2000)
  }

  const scraping   = scrapeJob?.status === 'scraping'  || scrapeJob?.status === 'queued'
  const scrapeDone = scrapeJob?.status === 'done'
  const predicting = predictJob?.status === 'running'  || predictJob?.status === 'queued'
  const ingesting  = ingestJob?.status  === 'ingesting' || ingestJob?.status === 'queued'

  return (
    <div className="page">
      <header className="site-header">
        <div className="header-inner">
          <h1>Belgian Staatsblad — RIA Scraper</h1>
          <p>Scrape Royal Decrees and classify their administrative burden</p>
        </div>
      </header>

      <main className="content">

        {/* ── Step 1: Configure ────────────────────────────────────────── */}
        <section className="card">
          <h2><span className="step">1</span> Configure Scraping</h2>

          <div className="form-grid">
            <label className="field">
              Start date
              <input type="date" value={form.start_date}
                onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
            </label>

            <label className="field">
              End date
              <input type="date" value={form.end_date}
                onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))} />
            </label>

            <label className="field">
              Document type
              <select value={form.doc_type}
                onChange={e => setForm(f => ({ ...f, doc_type: e.target.value }))}>
                {docTypes.map(t => <option key={t}>{t}</option>)}
              </select>
            </label>
          </div>

          <button className="btn primary"
            onClick={handleScrape}
            disabled={!form.start_date || !form.end_date || scraping}>
            {scraping ? 'Scraping…' : 'Scrape Documents'}
          </button>
        </section>

        {/* ── Step 2: Scrape status ─────────────────────────────────────── */}
        {scrapeJob && (
          <section className="card">
            <h2><span className="step">2</span> Scrape Status <StatusBadge status={scrapeJob.status} /></h2>

            {(scrapeJob.status === 'scraping' || scrapeJob.status === 'queued') && (
              <>
                <ProgressBar value={scrapeJob.progress || 0} />
                <p className="muted">{scrapeJob.progress_text || 'Starting…'} ({scrapeJob.progress || 0}%)</p>
              </>
            )}

            {scrapeJob.status === 'done' && (
              <p className="ok">Found <strong>{total}</strong> documents</p>
            )}

            {scrapeJob.status === 'error' && (
              <p className="err">Error: {scrapeJob.error}</p>
            )}
          </section>
        )}

        {/* ── Step 3: Preview + actions ─────────────────────────────────── */}
        {scrapeDone && (
          <section className="card">
            <h2><span className="step">3</span> Results Preview
              <small> (first 10 of {total})</small>
            </h2>

            {preview.length > 0 ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Ref #</th>
                      <th>Date</th>
                      <th>Title</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.map((row, i) => (
                      <tr key={i}>
                        <td>{row.ref_number}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{row.pub_date}</td>
                        <td>
                          <a href={row.url} target="_blank" rel="noreferrer">
                            {row.short_text}
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">No documents found for this date range.</p>
            )}

            <div className="action-row">
              <a className="btn secondary"
                href={`${API}/api/download/${scrapeJob.id}`}
                download>
                Download Scraping Results (Excel)
              </a>

              {/* Predictions run automatically after scrape — no manual button needed */}
            </div>
          </section>
        )}

        {/* ── Step 4: Predict + Store (auto-triggered after scrape) ────── */}
        {predictJob && (
          <section className="card">
            <h2><span className="step">4</span> Predict &amp; Store <StatusBadge status={predictJob.status} /></h2>

            {predicting && <p className="muted">{predictJob.progress_text || 'Running ML model…'}</p>}

            {predictJob.status === 'done' && (
              <>
                <p className="ok">Predictions complete.</p>
                {predictJob.progress_text && (
                  <p className="muted" style={{ fontSize: 13 }}>{predictJob.progress_text}</p>
                )}
                <div className="action-row">
                  <a className="btn secondary"
                    href={`${API}/api/download/${predictJob.id}`}
                    download>
                    Download Predictions (Excel)
                  </a>
                </div>
              </>
            )}

            {predictJob.status === 'error' && (
              <p className="err">Prediction failed: {predictJob.error}</p>
            )}
          </section>
        )}

        {/* ── Law DB indexing — auto-triggered, shown silently ──────────── */}
        {ingestJob && (
          <section className="card" style={{ borderLeft: '3px solid #2b6cb0' }}>
            <h2 style={{ fontSize: 15, marginBottom: 6 }}>
              Law Database Indexing <StatusBadge status={ingestJob.status} />
            </h2>

            {ingesting && (
              <>
                <ProgressBar value={50} />
                <p className="muted">{ingestJob.progress_text || 'Embedding articles…'}</p>
              </>
            )}

            {ingestJob.status === 'done' && (
              <p className="muted">
                <strong>{ingestJob.chunks_stored}</strong> new article chunks indexed &nbsp;·&nbsp;
                <strong>{ingestJob.db_total}</strong> total in law database
              </p>
            )}

            {ingestJob.status === 'error' && (
              <p className="err">Indexing failed: {ingestJob.error}</p>
            )}
          </section>
        )}

      </main>
    </div>
  )
}
