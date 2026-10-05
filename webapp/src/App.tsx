import axios from 'axios'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchAiAnalysis,
  fetchAlerts,
  fetchHealth,
  fetchPrice,
  fetchQuotes,
  fetchReconnaissance,
  fetchVerdict,
  updateSettings,
} from './api/client'
import type {
  AlertRow,
  AiAnalysisResponse,
  HealthResponse,
  LayerKey,
  MarketQuote,
  PairOption,
  PriceTick,
  ReconnaissanceResponse,
  ScanResult,
} from './api/types'
import { advanceStory } from './lib/story'
import { selectFocusedAlert, type LiquidityView } from './lib/decisionMap'
import Clippy from './components/Clippy'
import MacroPulse from './components/MacroPulse'
import PatternChecklist from './components/PatternChecklist'
import QuoteBoard from './components/QuoteBoard'
import Reconnaissance from './components/Reconnaissance'
import StrategyWorkspace from './components/StrategyWorkspace'
import StructureBadge from './components/StructureBadge'
import TgChart from './components/TgChart'
import VerdictCard from './components/VerdictCard'

// Toggleable chart layers. S/R + the two zone types carry priority filters
// below; the four moving averages are simple on/off overlays.
const LAYERS: { key: LayerKey; label: string }[] = [
  { key: 'sr', label: 'S/R lines' },
  { key: 'supply_demand', label: 'Demand & supply' },
  { key: 'fvg', label: 'Fair value gaps' },
  { key: 'decision_map', label: 'Decision map' },
  { key: 'ema9', label: 'EMA 9' },
  { key: 'ema21', label: 'EMA 21' },
  { key: 'ema50', label: 'EMA 50' },
  { key: 'sma200', label: 'SMA 200' },
]

type PriorityFilter = 'all' | 'high' | 'medium' | 'low'
type FutureView = 'projected' | 'execution' | 'both'
type SideTab = 'verdict' | 'patterns' | 'macro'

// Backend error code for a live feed that hasn't produced candles yet — a
// transient cold-connect state we retry through rather than surfacing.
function isFeedNotReady(error: unknown) {
  if (!axios.isAxiosError(error)) return false
  const detail = error.response?.data as { detail?: { code?: string } } | undefined
  return error.response?.status === 503 || detail?.detail?.code === 'feed_not_ready'
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

function apiErrorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    const payload = error.response?.data as { detail?: { message?: string } | string } | undefined
    const message =
      typeof payload?.detail === 'string'
        ? payload.detail
        : payload?.detail?.message ?? error.message
    if (!error.response) return `Network error: ${error.message}`
    return `${error.response.status}: ${message}`
  }
  return error instanceof Error ? error.message : 'scan failed'
}

function defaultPriority(layer: keyof Pick<ScanResult['layers'], 'sr' | 'supply_demand' | 'fvg'>): PriorityFilter {
  return layer === 'fvg' ? 'all' : 'high'
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [pair, setPair] = useState('')
  const [tf, setTf] = useState('')
  const [data, setData] = useState<ScanResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [booting, setBooting] = useState(true)
  const [layers, setLayers] = useState<Record<LayerKey, boolean>>({
    sr: true,
    supply_demand: true,
    fvg: true,
    decision_map: true,
    ema9: false,
    ema21: true,
    ema50: true,
    sma200: true,
  })
  const [priorityFilters, setPriorityFilters] = useState({
    sr: defaultPriority('sr'),
    supply_demand: defaultPriority('supply_demand'),
    fvg: defaultPriority('fvg'),
  })
  const [checkedPatterns, setCheckedPatterns] = useState<Record<string, boolean>>({})
  const [showPending, setShowPending] = useState(false)
  const [checkedChartPatterns, setCheckedChartPatterns] = useState<Record<string, boolean>>({})
  const [showFormingCharts, setShowFormingCharts] = useState(true)
  const [livePrice, setLivePrice] = useState<PriceTick | null>(null)
  const [futureView, setFutureView] = useState<FutureView>('both')
  const [sideTab, setSideTab] = useState<SideTab>('verdict')
  const [srFocus, setSrFocus] = useState(false)
  const [quotes, setQuotes] = useState<MarketQuote[]>([])
  const [quotesGeneratedAt, setQuotesGeneratedAt] = useState<number | null>(null)
  const [traderMode, setTraderMode] = useState('Swing Trader')
  const [aiProvider, setAiProvider] = useState('builtin')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [aiAnalysis, setAiAnalysis] = useState<AiAnalysisResponse | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [reconnaissance, setReconnaissance] = useState<ReconnaissanceResponse | null>(null)
  const [reconLoading, setReconLoading] = useState(false)
  const [reconError, setReconError] = useState<string | null>(null)
  const [alerts, setAlerts] = useState<AlertRow[]>([])
  const [liquidityView, setLiquidityView] = useState<LiquidityView>('nearby')
  const showExecutionPathHint =
    futureView !== 'projected' &&
    data?.verdict.plan?.status !== 'ENTER_READY'

  useEffect(() => {
    let cancelled = false
    async function boot() {
      setBooting(true)
      try {
        const next = await fetchHealth()
        if (cancelled) return
        setHealth(next)
        const initialPair = next.default_pair || next.pairs[0]?.id || ''
        setPair(curr => curr || initialPair)
        setTf(curr => curr || next.default_setup_tf || next.timeframes[0] || '')
        setTraderMode(next.default_trader_mode || 'Swing Trader')
        setAiProvider(next.default_ai_provider || 'builtin')
      } catch (e) {
        if (!cancelled) setError(apiErrorMessage(e))
      } finally {
        if (!cancelled) setBooting(false)
      }
    }
    void boot()
    return () => {
      cancelled = true
    }
  }, [])

  const load = useCallback(async (recheckReason?: string) => {
    if (!pair || !tf) return
    setLoading(true)
    setError(null)
    // A cold live feed often isn't ready on the first request after a pair
    // switch (socket connected, candle history still arriving). Retry a few
    // times before surfacing the error so first-load "feed_not_ready" is invisible.
    const MAX_TRIES = 3
    for (let attempt = 1; attempt <= MAX_TRIES; attempt++) {
      try {
        const result = await fetchVerdict(pair, tf, 2, traderMode, recheckReason)
        setData(result)
        setError(null)
        break
      } catch (e) {
        if (isFeedNotReady(e) && attempt < MAX_TRIES) {
          setError(`Feed warming up for ${pair}… retry ${attempt}/${MAX_TRIES - 1}`)
          await sleep(1800)
          continue
        }
        setData(null)
        setError(apiErrorMessage(e))
        break
      }
    }
    setLoading(false)
  }, [pair, tf, traderMode])

  const loadAiAnalysis = useCallback(async (forceRefresh?: boolean) => {
    if (!pair || !tf || aiProvider === 'none') {
      setAiAnalysis(null)
      return
    }
    setAiLoading(true)
    try {
      const result = await fetchAiAnalysis(pair, tf, traderMode, forceRefresh)
      setAiAnalysis(result)
    } catch {
      setAiAnalysis(null)
    } finally {
      setAiLoading(false)
    }
  }, [pair, tf, traderMode, aiProvider])

  const loadReconnaissance = useCallback(async (forceRefresh = false, signal?: AbortSignal) => {
    if (!pair) return null
    setReconLoading(true)
    setReconError(null)
    try {
      const result = await fetchReconnaissance(pair, forceRefresh, { signal })
      if (!signal?.aborted) setReconnaissance(result)
      return result
    } catch (e) {
      if (!signal?.aborted) setReconError(apiErrorMessage(e))
      return null
    } finally {
      if (!signal?.aborted) setReconLoading(false)
    }
  }, [pair])

  useEffect(() => {
    if (!pair || !tf) return
    void load('pair_change')
  }, [load])

  useEffect(() => {
    if (!pair || !tf || aiProvider === 'none') {
      setAiAnalysis(null)
      return
    }
    void loadAiAnalysis(false)
  }, [pair, tf, traderMode, aiProvider, data?.generated_at, loadAiAnalysis])

  useEffect(() => {
    if (!pair) return
    let cancelled = false
    let timer: number | null = null
    const controller = new AbortController()
    setReconnaissance(null)
    setReconError(null)

    const runAndSchedule = async () => {
      const result = await loadReconnaissance(false, controller.signal)
      if (cancelled) return
      const fallback = Math.floor(Date.now() / 1000) + 60
      const next = result?.next_refresh_at ?? fallback
      const delay = Math.max(1000, (next - Date.now() / 1000) * 1000)
      timer = window.setTimeout(() => void runAndSchedule(), delay)
    }

    void runAndSchedule()
    return () => {
      cancelled = true
      controller.abort()
      if (timer != null) window.clearTimeout(timer)
    }
  }, [pair, loadReconnaissance])

  useEffect(() => {
    if (!pair) return
    const controller = new AbortController()
    let cancelled = false
    const refresh = async () => {
      try {
        const result = await fetchAlerts(pair, 1, 20, { signal: controller.signal })
        if (!cancelled) setAlerts(result.alerts)
      } catch {
        // Alert history is an enhancement; candle inference remains available.
      }
    }
    setAlerts([])
    void refresh()
    const id = window.setInterval(() => void refresh(), 5000)
    return () => {
      cancelled = true
      controller.abort()
      window.clearInterval(id)
    }
  }, [pair])

  useEffect(() => {
    if (!data?.rescan || !pair || !tf) return
    const now = Math.floor(Date.now() / 1000)
    const nextTimes = [data.rescan.next_recheck_at]
    if (data.verdict.plan?.status === 'WAITING' && data.rescan.lower_tf_wakeup_enabled && data.rescan.lower_tf_next_recheck_at) {
      nextTimes.push(data.rescan.lower_tf_next_recheck_at)
    }
    const next = Math.min(...nextTimes.filter(Boolean))
    const delay = Math.max(1000, (next - now) * 1000 + 250)
    const reason =
      data.verdict.plan?.status === 'WAITING' &&
      data.rescan.lower_tf_wakeup_enabled &&
      data.rescan.lower_tf_next_recheck_at != null &&
      next === data.rescan.lower_tf_next_recheck_at
        ? 'lower_tf_wakeup'
        : 'entry_bar_close'
    const id = window.setTimeout(() => {
      void load(reason)
    }, delay)
    return () => window.clearTimeout(id)
  }, [data?.generated_at, data?.rescan, data?.verdict.plan?.status, pair, tf, load])

  // Stream the live TICK from the backend websocket when available; fall back
  // to the REST poll so the header still updates if websocket proxying fails.
  useEffect(() => {
    if (!pair) return
    setLivePrice(null) // don't show the previous pair's quote during the switch
    let cancelled = false
    let fallbackId: number | null = null
    let ws: WebSocket | null = null
    const tick = async () => {
      try {
        const p = await fetchPrice(pair, tf)
        if (!cancelled) setLivePrice(p)
      } catch {
        /* transient — keep last */
      }
    }

    try {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${window.location.host}/api/ws/price?pair=${encodeURIComponent(pair)}&tf=${encodeURIComponent(tf)}`)
      ws.onmessage = ev => {
        if (cancelled) return
        try {
          setLivePrice(JSON.parse(ev.data) as PriceTick)
        } catch {
          /* ignore malformed */
        }
      }
      ws.onerror = () => {
        if (fallbackId == null) {
          void tick()
          fallbackId = window.setInterval(() => void tick(), 1500)
        }
      }
      ws.onclose = () => {
        if (!cancelled && fallbackId == null) {
          void tick()
          fallbackId = window.setInterval(() => void tick(), 1500)
        }
      }
    } catch {
      void tick()
      fallbackId = window.setInterval(() => void tick(), 1500)
    }

    return () => {
      cancelled = true
      if (fallbackId != null) window.clearInterval(fallbackId)
      ws?.close()
    }
  }, [pair, tf])

  // Seed the pattern checklist from the instrument's catalog. New pattern names
  // default to checked; existing user choices are preserved across pair switches.
  useEffect(() => {
    const catalog = data?.pattern_catalog_flat
    if (!catalog) return
    setCheckedPatterns(prev => {
      let changed = false
      const next = { ...prev }
      for (const name of catalog) {
        if (!(name in next)) {
          next[name] = true
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [data?.pattern_catalog_flat])

  // Flat list of chart-pattern names (from the grouped catalog), for seeding + all/none.
  const chartPatternNames = useMemo(
    () => (data?.chart_pattern_catalog ?? []).flatMap(g => g.patterns.map(p => p.name)),
    [data?.chart_pattern_catalog],
  )

  // Seed the chart-pattern checklist; new names default to checked (shown).
  useEffect(() => {
    if (!chartPatternNames.length) return
    setCheckedChartPatterns(prev => {
      let changed = false
      const next = { ...prev }
      for (const name of chartPatternNames) {
        if (!(name in next)) {
          next[name] = true
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [chartPatternNames])

  useEffect(() => {
    let cancelled = false
    const refreshQuotes = async () => {
      try {
        const snapshot = await fetchQuotes(tf || undefined)
        if (!cancelled) {
          setQuotes(snapshot.quotes)
          setQuotesGeneratedAt(snapshot.generated_at)
        }
      } catch {
        /* Preserve the latest board snapshot through transient feed errors. */
      }
    }
    void refreshQuotes()
    const id = window.setInterval(() => void refreshQuotes(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [tf, health?.pairs.length])

  const handleTraderMode = useCallback(async (nextMode: string) => {
    setTraderMode(nextMode)
    try {
      await updateSettings({ trader_mode: nextMode })
      setNotice(`Trader mode set to ${nextMode}.`)
    } catch (e) {
      setNotice(apiErrorMessage(e))
    }
  }, [])

  const handleAiProvider = useCallback(async (nextProvider: string) => {
    setAiProvider(nextProvider)
    try {
      await updateSettings({ ai_provider: nextProvider })
      await load('manual_rescan')
      if (nextProvider !== 'none') {
        await loadAiAnalysis(true)
      } else {
        setAiAnalysis(null)
      }
      setNotice(`AI explainer set to ${nextProvider}.`)
    } catch (e) {
      setNotice(apiErrorMessage(e))
    }
  }, [load, loadAiAnalysis])

  const toggle = (k: LayerKey) => setLayers(s => ({ ...s, [k]: !s[k] }))
  const setPriority = (key: 'sr' | 'supply_demand' | 'fvg', value: PriorityFilter) =>
    setPriorityFilters(s => ({ ...s, [key]: value }))

  const togglePattern = (name: string) =>
    setCheckedPatterns(s => ({ ...s, [name]: !s[name] }))
  const setAllPatterns = (value: boolean) =>
    setCheckedPatterns(() =>
      Object.fromEntries((data?.pattern_catalog_flat ?? []).map(n => [n, value])),
    )

  const toggleChartPattern = (name: string) =>
    setCheckedChartPatterns(s => ({ ...s, [name]: !s[name] }))
  const setAllChartPatterns = (value: boolean) =>
    setCheckedChartPatterns(() => Object.fromEntries(chartPatternNames.map(n => [n, value])))

  const selectedPair = useMemo<PairOption | undefined>(
    () => health?.pairs.find(item => item.id === pair),
    [health?.pairs, pair],
  )

  const liveMode = health?.data_source === 'deriv'
  const activeChangePct = data?.change_pct
  const activeBasePrice = data?.price

  // Prefer the live quote (Deriv tick or Yahoo) when fresh; else the candle close.
  const tickFresh =
    (livePrice?.source === 'deriv' || livePrice?.source === 'yahoo') &&
    livePrice.price != null &&
    !livePrice.stale
  const shownPrice = tickFresh ? (livePrice!.price as number) : activeBasePrice ?? null
  const shownOhlc =
    livePrice?.ohlc ??
    (data?.candles?.length ? data.candles[data.candles.length - 1] : null)

  const focusedAlert = useMemo(
    () => selectFocusedAlert(
      alerts,
      data?.verdict.plan ?? null,
      tf,
      data?.candles?.[0]?.time ?? null,
    ),
    [alerts, data?.verdict.plan, data?.candles, tf],
  )

  // Ticked forward off the same price the header shows, so the assistant and
  // the verdict card can never disagree about which beat is live.
  const liveStory = useMemo(
    () =>
      advanceStory(data?.verdict.story, {
        price: shownPrice,
        alertState: focusedAlert?.state ?? null,
      }),
    [data?.verdict.story, shownPrice, focusedAlert?.state],
  )

  return (
    <div className="tg-app">
      <header className="tg-topbar">
        <span className="tg-brand">TradeGuard</span>
        <select value={pair} onChange={e => setPair(e.target.value)} className="tg-select">
          {(health?.pairs ?? []).map(p => (
            <option key={p.id} value={p.id}>
              {p.display}
            </option>
          ))}
        </select>
        <div className="tg-pills">
          {(health?.timeframes ?? []).map(t => (
            <button
              key={t}
              className={`tg-pill ${t === tf ? 'tg-pill--on' : ''}`}
              onClick={() => setTf(t)}
            >
              {t}
            </button>
          ))}
        </div>
        <label className="tg-filter">
          <span>mode</span>
          <select value={traderMode} onChange={e => void handleTraderMode(e.target.value)}>
            {Object.keys(health?.trader_modes ?? { 'Swing Trader': {} }).map(mode => (
              <option key={mode} value={mode}>{mode}</option>
            ))}
          </select>
        </label>
        <div className="tg-price">
          {shownPrice != null ? (
            <>
              {tickFresh && <span className="tg-live-dot" title="live tick" />}
              <strong>{shownPrice.toFixed(2)}</strong>
              {activeChangePct != null && (
                <span className={activeChangePct >= 0 ? 'tg-green' : 'tg-red'}>
                  {activeChangePct >= 0 ? '+' : ''}
                  {activeChangePct.toFixed(2)}%
                </span>
              )}
              <span className="tg-price-src">
                {tickFresh
                  ? livePrice!.source === 'yahoo'
                    ? 'live · yahoo'
                    : 'live'
                  : livePrice?.stale
                    ? 'candle (stale)'
                    : 'candle'}
              </span>
              {shownOhlc && (
                <span className="tg-ohlc">
                  <span>O {shownOhlc.open.toFixed(2)}</span>
                  <span>H {shownOhlc.high.toFixed(2)}</span>
                  <span>L {shownOhlc.low.toFixed(2)}</span>
                  <span>C {shownOhlc.close.toFixed(2)}</span>
                </span>
              )}
            </>
          ) : (
            <span className="tg-muted">—</span>
          )}
        </div>
        <button className="tg-rescan" onClick={() => void load('manual_rescan')} disabled={loading}>
          {loading ? 'Scanning…' : 'Re-scan'}
        </button>
        <button
          className={`tg-settings-btn ${settingsOpen ? 'tg-settings-btn--on' : ''}`}
          onClick={() => setSettingsOpen(v => !v)}
          aria-label="Open settings"
          title="Settings"
        >
          ⚙
        </button>
      </header>

      {settingsOpen && (
        <div className="tg-settings-pop">
          <div className="tg-settings-card">
            <div className="tg-settings-head">
              <strong>Settings</strong>
              <button className="tg-watch-tab-x" onClick={() => setSettingsOpen(false)} aria-label="Close settings">
                ×
              </button>
            </div>
            <label className="tg-settings-field">
              <span>Trader mode</span>
              <select value={traderMode} onChange={e => void handleTraderMode(e.target.value)}>
                {Object.keys(health?.trader_modes ?? { 'Swing Trader': {} }).map(mode => (
                  <option key={mode} value={mode}>{mode}</option>
                ))}
              </select>
            </label>
            <label className="tg-settings-field">
              <span>AI explainer</span>
              <select value={aiProvider} onChange={e => void handleAiProvider(e.target.value)}>
                {Object.entries(health?.ai_providers ?? { builtin: { label: 'Built-in explainer', status: 'ready' } }).map(([key, meta]) => (
                  <option key={key} value={key}>
                    {meta.label} {meta.free ? '· free' : ''} {meta.status !== 'ready' ? `· ${meta.status}` : ''}
                  </option>
                ))}
              </select>
            </label>
            <div className="tg-settings-notes">
              {Object.entries(health?.ai_providers ?? {}).map(([key, meta]) => (
                <p key={key}>
                  <strong>{meta.label}</strong>: {meta.detail}
                  {meta.model ? ` Model: ${meta.model}.` : ''}
                  {key === aiProvider ? ' Selected.' : ''}
                </p>
              ))}
            </div>
          </div>
        </div>
      )}

      {health && (
        <div className="tg-meta">
          <span>
            source: <strong>{health.data_source}</strong>
          </span>
            <span>
              pair: <strong>{selectedPair?.display ?? pair}</strong>
            </span>
            <span>
              mode: <strong>{traderMode}</strong>
            </span>
            <span>
              ai: <strong>{aiProvider}</strong>
            </span>
          {liveMode && (
            <span>
              feed: <strong>{health.feed_status.connected ? 'connected' : 'degraded'}</strong>
            </span>
          )}
          {liveMode && health.feed_status.error && (
            <span>
              reason: <strong>{health.feed_status.error}</strong>
            </span>
          )}
        </div>
      )}

      <QuoteBoard
        quotes={quotes}
        selected={pair}
        generatedAt={quotesGeneratedAt}
        onSelect={setPair}
      />

      <nav className="tg-layers">
        {LAYERS.map(l => (
          <button
            key={l.key}
            className={`tg-toggle ${layers[l.key] ? 'tg-toggle--on' : ''}`}
            onClick={() => toggle(l.key)}
          >
            {l.label}
          </button>
        ))}
        <label className="tg-filter">
          <span>Liquidity</span>
          <select value={liquidityView} onChange={e => setLiquidityView(e.target.value as LiquidityView)}>
            <option value="nearby">nearby</option>
            <option value="all">all</option>
            <option value="off">off</option>
          </select>
        </label>
        {([
          ['sr', 'S/R'],
          ['supply_demand', 'D/S'],
          ['fvg', 'FVG'],
        ] as const).map(([key, label]) => (
          <label key={key} className="tg-filter">
            <span>{label}</span>
            <select
              value={priorityFilters[key]}
              onChange={e => setPriority(key, e.target.value as PriorityFilter)}
            >
              <option value="all">all</option>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
            </select>
          </label>
        ))}
        <button
          className={`tg-toggle ${srFocus ? 'tg-toggle--on' : ''}`}
          onClick={() => setSrFocus(v => !v)}
        >
          S/R focus
        </button>
        {data && (
          <span className="tg-bias">
            <StructureBadge structure={data.structure} />
            <span className="tg-bias-meta">{data.future.entries.length} projected</span>
          </span>
        )}
        <label className="tg-filter">
          <span>future</span>
          <select value={futureView} onChange={e => setFutureView(e.target.value as FutureView)}>
            <option value="both">Both</option>
            <option value="projected">Projected setups</option>
            <option value="execution">Execution path</option>
          </select>
        </label>
        {showExecutionPathHint && (
          <span className="tg-bias-meta">Confirmed path appears only after every entry gate passes</span>
        )}
      </nav>

      {error && <div className="tg-error">{error}</div>}

      {data ? (
        <>
          <div className="tg-chart-row">
            <div className="tg-chart-col">
              <TgChart
                data={data}
                futureView={futureView}
                srFocus={srFocus}
                historyTrade={null}
                liveTrades={focusedAlert && ['WATCHING', 'TOUCHED', 'SWEPT', 'FILLED'].includes(focusedAlert.state) ? [focusedAlert] : []}
                focusedAlert={focusedAlert}
                story={liveStory}
                liquidityLevels={reconnaissance?.liquidity?.levels ?? []}
                liquidityView={liquidityView}
                layers={layers}
                priorityFilters={priorityFilters}
                checkedPatterns={checkedPatterns}
                showPending={showPending}
                checkedChartPatterns={checkedChartPatterns}
                showFormingCharts={showFormingCharts}
              />
            </div>
            <div className="tg-rail">
              <div className="tg-rail-tabs">
                <button
                  className={`tg-rail-tab ${sideTab === 'verdict' ? 'tg-rail-tab--on' : ''}`}
                  onClick={() => setSideTab('verdict')}
                >
                  Verdict
                </button>
                <button
                  className={`tg-rail-tab ${sideTab === 'patterns' ? 'tg-rail-tab--on' : ''}`}
                  onClick={() => setSideTab('patterns')}
                >
                  Patterns
                </button>
                <button
                  className={`tg-rail-tab ${sideTab === 'macro' ? 'tg-rail-tab--on' : ''}`}
                  onClick={() => setSideTab('macro')}
                >
                  Macro Pulse
                </button>
              </div>
              {sideTab === 'verdict' ? (
                <div className="tg-rail-pane">
                  <VerdictCard
                    verdict={data.verdict}
                    generatedAt={data.generated_at}
                    rescan={data.rescan}
                    compact
                    livePrice={shownPrice}
                    alertState={focusedAlert?.state ?? null}
                  />
                </div>
              ) : sideTab === 'patterns' ? (
                <div className="tg-pattern-panes">
                  <PatternChecklist
                    catalog={data.chart_pattern_catalog}
                    checked={checkedChartPatterns}
                    onToggle={toggleChartPattern}
                    onSetAll={setAllChartPatterns}
                    showPending={showFormingCharts}
                    onShowPending={setShowFormingCharts}
                    title="Chart patterns"
                    pendingLabel="Show forming (unconfirmed)"
                    note="A chart pattern confirms only when price closes beyond its boundary (neckline / trendline / rail)."
                  />
                  <PatternChecklist
                    catalog={data.pattern_catalog}
                    checked={checkedPatterns}
                    onToggle={togglePattern}
                    onSetAll={setAllPatterns}
                    showPending={showPending}
                    onShowPending={setShowPending}
                    title="Candlestick patterns"
                  />
                </div>
              ) : (
                <MacroPulse data={data} health={health} livePrice={livePrice} />
              )}
            </div>
          </div>
          <Reconnaissance
            report={reconnaissance}
            loading={reconLoading}
            error={reconError}
            onRefresh={() => void loadReconnaissance(true)}
          />
          <StrategyWorkspace
            data={data}
            analysis={aiAnalysis}
            loading={aiLoading}
            onRefresh={() => void loadAiAnalysis(true)}
          />
        </>
      ) : (
        !error && <div className="tg-loading">{booting ? 'Loading market config…' : 'Loading scan…'}</div>
      )}

      {notice && <div className="tg-trade-msg">{notice}</div>}

      <footer className="tg-foot">
        Analysis and education only — not financial advice. TradeGuard guides strategy review
        and does not place or manage trades.
      </footer>

      <Clippy story={liveStory} />
    </div>
  )
}
