import { useCallback, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  fetchPriceForecastRun, fetchPriceForecastRuns, fetchResearchBackfill,
  fetchResearchDataStatus, fetchResearchHistory, fetchResearchRun, fetchResearchRuns,
  startPriceForecast, startResearchBackfill, startResearchRun,
} from '../api/client'
import type { PriceForecastRun, ResearchDataJob, ResearchDataStatus, ResearchHistory, ResearchModeResult, ResearchRun } from '../api/types'

const MODELS = ['constant', 'segment', 'logistic', 'lightgbm'] as const

function fmtDate(epoch?: number | null) {
  return epoch ? new Date(epoch * 1000).toLocaleString() : '—'
}

function fmtMetric(value?: number | null) {
  return value == null ? '—' : value.toFixed(4)
}

function PriceForecast({ run }: { run: PriceForecastRun }) {
  const points = run.points ?? []
  if (!points.length) return <p className="tg-research-note">{run.data?.error ?? run.error ?? 'No held-out forecast points are available yet.'}</p>
  const forecast = run.data?.future_forecast
  const modelName = String(run.data?.model_name ?? 'lightgbm') as 'lightgbm' | 'hist_gradient_boosting'
  const series = [
    { key: 'actual', label: 'Actual close', color: '#e8edf8' },
    { key: 'last_close', label: 'Last-close baseline', color: '#f4b942' },
    { key: modelName, label: modelName === 'lightgbm' ? 'LightGBM' : 'Histogram boosting fallback', color: '#59d6a3' },
  ] as const
  const values = points.flatMap(point => [point.actual, point.last_close, point.lightgbm, point.hist_gradient_boosting].filter((v): v is number => typeof v === 'number'))
  if (forecast) values.push(forecast.intervals['90'].lower, forecast.intervals['90'].upper, forecast.last_close, forecast.prediction)
  const low = Math.min(...values)
  const high = Math.max(...values)
  const span = Math.max(high - low, Math.abs(high) * 0.0001, 0.01)
  const line = (key: typeof series[number]['key']) => points.flatMap((point, index) => {
    const value = point[key]
    if (typeof value !== 'number') return []
    const x = (index / Math.max(1, points.length - 1)) * (forecast ? 790 : 1000)
    const y = 220 - ((value - low) / span) * 200
    return [`${x.toFixed(1)},${y.toFixed(1)}`]
  }).join(' ')
  const chartY = (value: number) => 220 - ((value - low) / span) * 200
  const fmtPrice = (value: number) => value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const zone = forecast?.intervals
  return <>
    <div className="tg-forecast-legend">{series.map(item => <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>)}</div>
    <div className="tg-forecast-chart"><svg viewBox="0 0 1000 240" preserveAspectRatio="none" role="img" aria-label="Historical test forecasts and next-hour projected price zones">
      {[0, 1, 2, 3].map(tick => <line key={tick} x1="0" x2="1000" y1={20 + tick * 66} y2={20 + tick * 66} className="tg-chart-gridline" />)}
      {forecast && zone && <>
        <rect x="805" width="175" y={chartY(zone['90'].upper)} height={Math.max(1, chartY(zone['90'].lower) - chartY(zone['90'].upper))} className="tg-forecast-zone-90" />
        <rect x="805" width="175" y={chartY(zone['50'].upper)} height={Math.max(1, chartY(zone['50'].lower) - chartY(zone['50'].upper))} className="tg-forecast-zone-50" />
        <line x1="790" x2="980" y1={chartY(forecast.prediction)} y2={chartY(forecast.prediction)} className="tg-forecast-projection" />
        <circle cx="875" cy={chartY(forecast.prediction)} r="4" className="tg-forecast-projection-dot" />
        <text x="808" y="14" className="tg-forecast-next-label">NEXT 1H</text>
      </>}
      {series.map(item => <polyline key={item.key} points={line(item.key)} fill="none" stroke={item.color} strokeWidth={item.key === 'actual' ? 2.5 : 1.7} vectorEffect="non-scaling-stroke" />)}
    </svg></div>
    <div className="tg-forecast-dates"><span>{new Date(points[0].epoch * 1000).toLocaleDateString()}</span><span>{new Date(points[points.length - 1].epoch * 1000).toLocaleDateString()}</span></div>
    {forecast && zone && <div className="tg-forecast-callout">
      <div className={`tg-forecast-bias tg-forecast-bias--${forecast.bias === 'BUY BIAS' ? 'buy' : forecast.bias === 'SELL BIAS' ? 'sell' : 'neutral'}`}>
        <span>Next-hour directional read</span><strong>{forecast.bias}</strong>
        <p>Up {Math.round(forecast.probability_up * 100)}% <i>·</i> Down {Math.round(forecast.probability_down * 100)}%</p>
      </div>
      <div className="tg-forecast-levels">
        <div><span>Last close</span><b>{fmtPrice(forecast.last_close)}</b></div>
        <div><span>Point estimate</span><b>{fmtPrice(forecast.prediction)}</b></div>
        <div><span><i className="tg-zone-key tg-zone-key--50" />50% zone</span><b>{fmtPrice(zone['50'].lower)} – {fmtPrice(zone['50'].upper)}</b></div>
        <div><span><i className="tg-zone-key tg-zone-key--90" />90% zone</span><b>{fmtPrice(zone['90'].lower)} – {fmtPrice(zone['90'].upper)}</b></div>
      </div>
      <p className="tg-research-note">Empirically calibrated from {forecast.calibration_samples} validation errors; zones are not guarantees. Data age: {forecast.stale_hours.toFixed(1)}h. Research only—not a live trading instruction.</p>
    </div>}
  </>
}

function PriceForecastResult({ run }: { run: PriceForecastRun }) {
  const sourceLabel = run.source === 'yahoo' ? 'Yahoo GC=F gold-futures proxy' : 'Deriv XAU/USD'
  return <article className="tg-research-result tg-price-forecast-result">
    <div className="tg-research-result-head"><div><span className="tg-eyebrow">One-hour-ahead direction &amp; price zones · {sourceLabel}</span><h3>{run.data?.future_forecast?.bias ?? run.status.replace(/_/g, ' ')}</h3></div><span className={`tg-research-state tg-research-state--${run.status === 'completed' ? 'qualified_for_shadow' : 'insufficient_data'}`}>{run.status.replace(/_/g, ' ')}</span></div>
    {run.data?.source_note && <p className="tg-research-note">{run.data.source_note}</p>}
    {run.error && <p className="tg-research-blocker">{run.error}</p>}
    {run.data?.model_error && <p className="tg-research-blocker">{run.data.model_error}</p>}
    {Object.keys(run.metrics ?? {}).length > 0 && <div className="tg-research-table-wrap"><table className="tg-research-table"><thead><tr><th>Model</th><th>MSE ↓</th><th>MAE ↓</th><th>RMSE ↓</th><th>Direction ↑</th><th>Up Brier ↓</th><th>Up AUC</th><th>Test points</th></tr></thead><tbody>{Object.entries(run.metrics).map(([name, metrics]) => <tr key={name}><td>{name.replace(/_/g, ' ')}</td><td>{fmtMetric(metrics.mse)}</td><td>{fmtMetric(metrics.mae)}</td><td>{fmtMetric(metrics.rmse)}</td><td>{(metrics.directional_accuracy * 100).toFixed(1)}%</td><td>{fmtMetric(metrics.up_probability_brier)}</td><td>{fmtMetric(metrics.up_probability_roc_auc)}</td><td>{metrics.test_points}</td></tr>)}</tbody></table></div>}
    <PriceForecast run={run} />
    <p className="tg-research-note">Test period: {fmtDate(Number(run.data?.test_start_epoch))} – {fmtDate(Number(run.data?.test_end_epoch))}. Chronological split; no shuffled samples.</p>
  </article>
}

function errorText(error: unknown) {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 404 && error.config?.url?.includes('/research/')) {
      return 'The backend currently serving this page does not have the Research Lab API routes (404). Restart TradeGuard with ./start.sh restart, then reload this page.'
    }
    const detail = error.response?.data?.detail
    return typeof detail === 'string' ? detail : detail?.message ?? error.message
  }
  return error instanceof Error ? error.message : 'Research request failed.'
}

function ModeResult({ result }: { result: ResearchModeResult }) {
  const qualification = result.metrics.qualification
  return (
    <article className="tg-research-result">
      <div className="tg-research-result-head">
        <div>
          <span className="tg-eyebrow">{result.trader_mode}</span>
          <h3>{result.status.replace(/_/g, ' ')}</h3>
        </div>
        <span className={`tg-research-state tg-research-state--${result.status}`}>{result.status.replace(/_/g, ' ')}</span>
      </div>
      <div className="tg-research-metrics">
        <div><span>Resolved</span><strong>{result.sample_count}</strong></div>
        <div><span>Independent dates</span><strong>{result.independent_days}</strong></div>
        <div><span>Target / stopped</span><strong>{result.positive_count} / {result.negative_count}</strong></div>
        <div><span>Censored</span><strong>{result.censored_count}</strong></div>
      </div>
      {Object.keys(result.metrics).length > 0 && (
        <div className="tg-research-table-wrap">
          <table className="tg-research-table">
            <thead><tr><th>Model</th><th>Brier ↓</th><th>Log loss ↓</th><th>ROC-AUC</th><th>PR-AUC</th></tr></thead>
            <tbody>
              {MODELS.map(name => {
                const metric = result.metrics[name] as { brier?: number; log_loss?: number; roc_auc?: number | null; pr_auc?: number | null } | undefined
                if (!metric) return null
                return <tr key={name}><td>{name}</td><td>{fmtMetric(metric.brier)}</td><td>{fmtMetric(metric.log_loss)}</td><td>{fmtMetric(metric.roc_auc)}</td><td>{fmtMetric(metric.pr_auc)}</td></tr>
              })}
            </tbody>
          </table>
        </div>
      )}
      {qualification?.lightgbm_relative_brier_improvement != null && (
        <p className="tg-research-note">LightGBM Brier improvement over the best baseline: {(qualification.lightgbm_relative_brier_improvement * 100).toFixed(1)}% (5% required).</p>
      )}
      {typeof result.metrics.lightgbm_net_r_after_spread === 'number' && (
        <p className="tg-research-note">LightGBM paper replay: {result.metrics.lightgbm_net_r_after_spread.toFixed(2)}R after estimated XAU/USD spread.</p>
      )}
      {qualification?.lightgbm_minus_baseline_brier_day_bootstrap_95ci && (
        <p className="tg-research-note">Day-block bootstrap 95% CI for Brier difference (LightGBM − baseline): {qualification.lightgbm_minus_baseline_brier_day_bootstrap_95ci.map(value => value?.toFixed(4) ?? '—').join(' to ')}.</p>
      )}
      {result.blockers.length > 0 && <ul className="tg-research-blockers">{result.blockers.map(item => <li key={item}>{item}</li>)}</ul>}
      {result.feature_importance.length > 0 && (
        <div className="tg-research-importance">
          <strong>Feature importance</strong>
          {result.feature_importance.slice(0, 8).map(item => <div key={item.feature}><span>{item.feature}</span><meter min={0} max={1} value={item.gain} /><b>{(item.gain * 100).toFixed(1)}%</b></div>)}
        </div>
      )}
      {result.folds.length > 0 && <p className="tg-research-note">{result.folds.length} valid purged walk-forward fold(s), latest test through {result.folds[result.folds.length - 1]?.test_end}.</p>}
    </article>
  )
}

export default function ResearchLab({ pair, onSelectXau }: { pair: string; onSelectXau: () => void }) {
  const [status, setStatus] = useState<ResearchDataStatus | null>(null)
  const [runs, setRuns] = useState<ResearchRun[]>([])
  const [active, setActive] = useState<ResearchRun | null>(null)
  const [history, setHistory] = useState<ResearchHistory | null>(null)
  const [dataJob, setDataJob] = useState<ResearchDataJob | null>(null)
  const [forecastRuns, setForecastRuns] = useState<PriceForecastRun[]>([])
  const [forecastActive, setForecastActive] = useState<PriceForecastRun | null>(null)
  const [loading, setLoading] = useState(false)
  const [dataLoading, setDataLoading] = useState(false)
  const [forecastLoading, setForecastLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (pair !== 'xauusd') return
    try {
      const [nextStatus, nextRuns, nextHistory, nextForecastRuns] = await Promise.all([
        fetchResearchDataStatus(pair), fetchResearchRuns(pair), fetchResearchHistory(pair), fetchPriceForecastRuns(pair),
      ])
      setStatus(nextStatus)
      setRuns(nextRuns)
      setHistory(nextHistory)
      setDataJob(nextHistory.active_job ?? nextHistory.jobs[0] ?? null)
      setForecastRuns(nextForecastRuns)
      setForecastActive(nextForecastRuns[0] ?? null)
      const current = nextRuns.find(run => run.status === 'queued' || run.status === 'running') ?? nextRuns[0]
      if (current) setActive(await fetchResearchRun(current.id))
      setError(null)
    } catch (e) {
      setError(errorText(e))
    }
  }, [pair])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!active || !['queued', 'running'].includes(active.status)) return
    const id = window.setInterval(async () => {
      try {
        const next = await fetchResearchRun(active.id)
        setActive(next)
        if (!['queued', 'running'].includes(next.status)) void load()
      } catch { /* keep the last truthful job state */ }
    }, 2000)
    return () => window.clearInterval(id)
  }, [active?.id, active?.status, load])

  useEffect(() => {
    if (!dataJob || !['queued', 'running'].includes(dataJob.status)) return
    const id = window.setInterval(async () => {
      try {
        const next = await fetchResearchBackfill(dataJob.id)
        setDataJob(next)
        if (!['queued', 'running'].includes(next.status)) void load()
      } catch { /* keep the last visible progress */ }
    }, 2000)
    return () => window.clearInterval(id)
  }, [dataJob?.id, dataJob?.status, load])

  useEffect(() => {
    if (!forecastActive || !['queued', 'running'].includes(forecastActive.status)) return
    const id = window.setInterval(async () => {
      try {
        const next = await fetchPriceForecastRun(forecastActive.id)
        setForecastActive(next)
        if (!['queued', 'running'].includes(next.status)) void load()
      } catch { /* keep the last visible forecast state */ }
    }, 2000)
    return () => window.clearInterval(id)
  }, [forecastActive?.id, forecastActive?.status, load])

  const allModes = useMemo(() => status?.modes.map(mode => mode.trader_mode) ?? [], [status])
  async function run() {
    setLoading(true); setError(null)
    try {
      const next = await startResearchRun('xauusd', allModes)
      setActive(next)
      await load()
    } catch (e) { setError(errorText(e)) } finally { setLoading(false) }
  }

  async function fetchHistory() {
    setDataLoading(true); setError(null)
    try {
      const next = await startResearchBackfill('xauusd', 2)
      setDataJob(next)
      await load()
    } catch (e) { setError(errorText(e)) } finally { setDataLoading(false) }
  }

  async function runForecast() {
    setForecastLoading(true); setError(null)
    try {
      const next = await startPriceForecast('xauusd')
      setForecastActive(next)
      await load()
    } catch (e) { setError(errorText(e)) } finally { setForecastLoading(false) }
  }

  if (pair !== 'xauusd') return <section className="tg-research-lab tg-research-scope"><span className="tg-eyebrow">Research Lab</span><h2>XAU/USD scope only</h2><p>The first auditable setup-quality benchmark is intentionally isolated to the market with the deepest recorded history.</p><button className="tg-rescan" onClick={onSelectXau}>Switch to XAU/USD</button></section>

  return (
    <section className="tg-research-lab">
      <header className="tg-research-head">
        <div><span className="tg-eyebrow">Offline model evaluation</span><h2>XAU/USD Setup-Quality Research Lab</h2><p>Target-before-stop classification after fill, evaluated separately for each trader mode. Unfilled and unresolved episodes stay censored.</p></div>
        <div className="tg-research-actions"><span className="tg-research-only">Research only · never changes live verdicts</span><button className="tg-rescan" onClick={() => void run()} disabled={loading || active?.status === 'queued' || active?.status === 'running'}>{active?.status === 'running' ? 'Benchmark running…' : loading ? 'Starting…' : 'Run all modes'}</button></div>
      </header>
      {error && <div className="tg-error">{error}</div>}
      {!status && !error && <div className="tg-research-empty">Loading research data status…</div>}
      {error && !status && <div className="tg-research-empty"><strong>Research data is unavailable.</strong><p>The page is connected, but it could not load the backend status and run history. After restarting the backend, retry here.</p><button className="tg-rescan" onClick={() => void load()}>Retry</button></div>}
      {history && <article className="tg-research-data-card tg-history-acquisition">
        <div className="tg-research-result-head"><div><span className="tg-eyebrow">Historical input</span><h3>One-hour XAU/USD history</h3><p>Fetch up to two years. Deriv is preferred; Yahoo GC=F is used only if the Deriv series is too short and remains a separate futures-proxy dataset.</p></div><button className="tg-rescan" onClick={() => void fetchHistory()} disabled={dataLoading || dataJob?.status === 'queued' || dataJob?.status === 'running' || forecastActive?.status === 'queued' || forecastActive?.status === 'running'}>{dataJob?.status === 'running' ? 'Fetching history…' : dataLoading ? 'Starting…' : 'Fetch 1H history'}</button></div>
        <div className="tg-history-source-grid">{(['deriv', 'yahoo'] as const).map(source => {
          const item = history.history.sources[source]
          return <div key={source} className={`tg-history-source ${history.history.selected_source === source ? 'is-selected' : ''}`}><strong>{source === 'deriv' ? 'Deriv · XAU/USD' : 'Yahoo · GC=F proxy'}</strong><span>{item.rows.toLocaleString()} candles</span><small>{item.first_epoch ? `${new Date(item.first_epoch * 1000).toLocaleDateString()} – ${new Date((item.last_epoch ?? item.first_epoch) * 1000).toLocaleDateString()}` : 'No stored history'}</small><small>{item.usable_for_forecast ? 'Ready for forecast' : `Need ${history.history.minimum_candles} candles`}</small></div>
        })}</div>
        {history.history.note && <p className="tg-research-note">{history.history.note}</p>}
        {dataJob && <p className="tg-research-note">History job #{dataJob.id}: {dataJob.status} · {dataJob.message ?? 'waiting'}{dataJob.error ? ` · ${dataJob.error}` : ''}</p>}
      </article>}
      <article className="tg-research-data-card tg-price-forecast-panel">
        <div className="tg-research-result-head"><div><span className="tg-eyebrow">Notebook-style prediction evaluation</span><h3>Actual vs. predicted close</h3><p>One bar ahead on 1H candles, with a 30-bar lookback. Compare LightGBM against a last-close baseline on a chronological holdout.</p></div><button className="tg-rescan" onClick={() => void runForecast()} disabled={forecastLoading || !history?.history.selected_source || dataJob?.status === 'queued' || dataJob?.status === 'running' || forecastActive?.status === 'queued' || forecastActive?.status === 'running'}>{forecastActive?.status === 'running' ? 'Evaluating…' : forecastLoading ? 'Starting…' : 'Run price forecast'}</button></div>
        {!history?.history.selected_source && <p className="tg-research-note">Fetch enough single-source 1H candles before running forecast metrics.</p>}
        {forecastActive && <PriceForecastResult run={forecastActive} />}
        {forecastRuns.length > 1 && <div className="tg-research-history"><strong>Forecast history</strong>{forecastRuns.slice(1).map(item => <button key={item.id} onClick={async () => setForecastActive(await fetchPriceForecastRun(item.id))}>#{item.id} · {item.source} · {item.status} · {fmtDate(item.created_at)}</button>)}</div>}
      </article>
      {status && <>
        <div className="tg-research-summary"><div><span>Journal rows</span><strong>{status.journal_rows}</strong></div><div><span>Independent journal dates</span><strong>{status.journal_days}</strong></div><div><span>Label horizon</span><strong>{status.label_horizon_bars} entry bars</strong></div><div><span>Model family</span><strong>{status.model_kind}</strong></div></div>
        <div className="tg-research-mode-grid">{status.modes.map(mode => <article key={mode.trader_mode} className="tg-research-data-card"><span className="tg-eyebrow">{mode.trader_mode}</span><h3>Data health</h3>{Object.entries(mode.frames).map(([tf, frame]) => <div className="tg-research-frame" key={tf}><strong>{tf}</strong><span>{frame.valid_rows.toLocaleString()} valid · {frame.days} days</span><span>{frame.off_grid.toLocaleString()} off-grid · {frame.gaps} gaps</span><small>{frame.selected_source}</small></div>)}{mode.blockers.length > 0 && <ul className="tg-research-blockers">{mode.blockers.map(item => <li key={item}>{item}</li>)}</ul>}</article>)}</div>
      </>}
      {active && <div className="tg-research-run-head"><div><span className="tg-eyebrow">Selected run #{active.id}</span><h3>{active.status}</h3><p>Started {fmtDate(active.started_at ?? active.created_at)}{active.completed_at ? ` · completed ${fmtDate(active.completed_at)}` : ''}</p></div>{active.error && <span className="tg-red">{active.error}</span>}</div>}
      <div className="tg-research-results">{active?.results?.map(result => <ModeResult key={result.trader_mode} result={result} />)}</div>
      {runs.length > 0 && <div className="tg-research-history"><strong>Run history</strong>{runs.map(run => <button key={run.id} onClick={async () => setActive(await fetchResearchRun(run.id))} className={active?.id === run.id ? 'is-active' : ''}>#{run.id} · {run.status} · {fmtDate(run.created_at)}</button>)}</div>}
    </section>
  )
}
