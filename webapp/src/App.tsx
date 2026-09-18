import axios from 'axios'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  closeTrade,
  fetchAiAnalysis,
  fetchAlerts,
  fetchHealth,
  fetchMonitor,
  fetchPrice,
  fetchStatsRange,
  fetchVerdict,
  setMonitor,
  takeTrade,
  updateSettings,
} from './api/client'
import type {
  AlertRow,
  AiAnalysisResponse,
  ClosedTradeRow,
  HealthResponse,
  LayerKey,
  MonitorStatus,
  Pagination,
  PairOption,
  PriceTick,
  ScanResult,
  StatsResponse,
} from './api/types'
import { advanceStory, TERMINAL_ALERT } from './lib/story'
import Clippy from './components/Clippy'
import MacroPulse from './components/MacroPulse'
import PatternChecklist from './components/PatternChecklist'
import PositionsPanel from './components/PositionsPanel'
import StructureBadge from './components/StructureBadge'
import TgChart from './components/TgChart'
import VerdictCard from './components/VerdictCard'

// Toggleable chart layers. S/R + the two zone types carry priority filters
// below; the four moving averages are simple on/off overlays.
const LAYERS: { key: LayerKey; label: string }[] = [
  { key: 'sr', label: 'S/R lines' },
  { key: 'supply_demand', label: 'Demand & supply' },
  { key: 'fvg', label: 'Fair value gaps' },
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
  const [monitor, setMonitorState] = useState<MonitorStatus | null>(null)
  const [alerts, setAlerts] = useState<AlertRow[]>([])
  const [alertsPage, setAlertsPage] = useState<Pagination>({
    page: 1,
    per_page: 8,
    total: 0,
    total_pages: 1,
  })
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [ledgerRange, setLedgerRange] = useState<{ from: string; to: string }>({
    from: '',
    to: '',
  })
  const [watchedPairs, setWatchedPairs] = useState<string[]>([])
  const [traderMode, setTraderMode] = useState('Swing Trader')
  const [aiProvider, setAiProvider] = useState('builtin')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [taking, setTaking] = useState(false)
  const [closingId, setClosingId] = useState<number | null>(null)
  const [tradeMsg, setTradeMsg] = useState<string | null>(null)
  const [historyTrade, setHistoryTrade] = useState<ClosedTradeRow | null>(null)
  const [aiAnalysis, setAiAnalysis] = useState<AiAnalysisResponse | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const monitorRefreshInFlight = useRef(false)
  const monitorAbortRef = useRef<AbortController | null>(null)
  const liveTradesForPair = useMemo(
    () =>
      alerts
        .filter(a => a.pair === pair && a.state === 'FILLED')
        .sort((a, b) => a.armed_at - b.armed_at),
    [alerts, pair],
  )
  // The alert the storyteller reads its beat progress from: the live one for
  // this pair on the planned side. `alerts` is already polled every 4s below.
  const storyAlert = useMemo(
    () =>
      alerts.find(
        a =>
          a.pair === pair &&
          !TERMINAL_ALERT.has(a.state) &&
          (!data?.verdict.plan?.side || a.side === data.verdict.plan.side),
      ) ?? null,
    [alerts, pair, data?.verdict.plan?.side],
  )
  const showExecutionPathHint =
    futureView !== 'projected' &&
    liveTradesForPair.length === 0 &&
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
        setWatchedPairs(curr => (curr.length ? curr : initialPair ? [initialPair] : []))
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

  // Poll the always-on monitor + its alerts/account so the alert state machine
  // and the paper ledger update live, without a re-scan.
  const refreshMonitor = useCallback(async () => {
    if (monitorRefreshInFlight.current) return
    monitorAbortRef.current?.abort()
    const controller = new AbortController()
    monitorAbortRef.current = controller
    monitorRefreshInFlight.current = true
    try {
      const m = await fetchMonitor({ signal: controller.signal })
      setMonitorState(m)
      if (pair) {
        const [a, st] = await Promise.all([
          fetchAlerts(pair, alertsPage.page, alertsPage.per_page, { signal: controller.signal }),
          fetchStatsRange(pair, ledgerRange.from || undefined, ledgerRange.to || undefined, { signal: controller.signal }),
        ])
        setAlerts(a.alerts)
        setAlertsPage(a.pagination)
        setStats(st)
      } else {
        setAlerts([])
        setAlertsPage(prev => ({ ...prev, total: 0, total_pages: 1 }))
        setStats(null)
      }
    } catch (e) {
      if (axios.isCancel(e) || (e instanceof Error && e.name === 'CanceledError')) return
      /* transient — keep the last snapshot */
    } finally {
      if (monitorAbortRef.current === controller) {
        monitorAbortRef.current = null
      }
      monitorRefreshInFlight.current = false
    }
  }, [alertsPage.page, alertsPage.per_page, ledgerRange.from, ledgerRange.to, pair])

  useEffect(() => {
    void refreshMonitor()
    const id = window.setInterval(() => void refreshMonitor(), 6000)
    return () => {
      window.clearInterval(id)
      monitorAbortRef.current?.abort()
      monitorAbortRef.current = null
      monitorRefreshInFlight.current = false
    }
  }, [refreshMonitor])

  const handleTake = useCallback(async () => {
    const plan = data?.verdict.plan
    if (!plan || !plan.side || plan.entry == null || plan.stop == null || plan.target == null || !pair || !tf) return
    setTaking(true)
    setTradeMsg(null)
    try {
      const res = await takeTrade({
        pair,
        tf,
        side: plan.side as 'long' | 'short',
        stop: plan.stop,
        target: plan.target,
      })
      setTradeMsg(
        `Paper ${res.side} opened on ${res.pair} @ ${res.entry} · stop ${res.stop} · target ${res.target} (${res.rr}R).`,
      )
      await refreshMonitor()
    } catch (e) {
      setTradeMsg(apiErrorMessage(e))
    } finally {
      setTaking(false)
    }
  }, [data, pair, tf, refreshMonitor])

  const handleClose = useCallback(
    async (id: number) => {
      setClosingId(id)
      try {
        await closeTrade(id)
        await refreshMonitor()
      } catch (e) {
        setTradeMsg(apiErrorMessage(e))
      } finally {
        setClosingId(null)
      }
    },
    [refreshMonitor],
  )

  const handleAlertsPage = useCallback((page: number) => {
    setAlertsPage(prev => ({ ...prev, page }))
  }, [])

  const handleLedgerRange = useCallback((from: string, to: string) => {
    setLedgerRange({ from, to })
  }, [])

  const handleShowHistory = useCallback((row: ClosedTradeRow | null) => {
    setHistoryTrade(row)
  }, [])

  const isWatched = useMemo(() => watchedPairs.includes(pair), [watchedPairs, pair])

  const pinCurrentPair = useCallback(() => {
    if (!pair) return
    setWatchedPairs(prev => (prev.includes(pair) ? prev : [...prev, pair]))
  }, [pair])

  const unpinPair = useCallback((pairId: string) => {
    setWatchedPairs(prev => {
      const next = prev.filter(p => p !== pairId)
      return next.length ? next : prev
    })
  }, [])

  const activateMonitorPair = useCallback(async (pairId: string) => {
    const timeframe = tf || health?.default_setup_tf || '4H'
    try {
      await setMonitor(pairId, timeframe, traderMode)
      setTradeMsg(`Live monitor switched to ${pairId.toUpperCase()}.`)
      await refreshMonitor()
    } catch (e) {
      setTradeMsg(apiErrorMessage(e))
    }
  }, [tf, health?.default_setup_tf, refreshMonitor, traderMode])

  const handleTraderMode = useCallback(async (nextMode: string) => {
    setTraderMode(nextMode)
    try {
      await updateSettings({ trader_mode: nextMode })
      if (pair) {
        const timeframe = tf || health?.default_setup_tf || '4H'
        await setMonitor(pair, timeframe, nextMode)
        await refreshMonitor()
      }
      setTradeMsg(`Trader mode set to ${nextMode}.`)
    } catch (e) {
      setTradeMsg(apiErrorMessage(e))
    }
  }, [pair, tf, health?.default_setup_tf, refreshMonitor])

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
      setTradeMsg(`AI explainer set to ${nextProvider}.`)
    } catch (e) {
      setTradeMsg(apiErrorMessage(e))
    }
  }, [load, loadAiAnalysis])

  const selectWatchedPair = useCallback((pairId: string) => {
    setPair(pairId)
    setWatchedPairs(prev => (prev.includes(pairId) ? prev : [...prev, pairId]))
  }, [])

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

  // Ticked forward off the same price the header shows, so the assistant and
  // the verdict card can never disagree about which beat is live.
  const liveStory = useMemo(
    () =>
      advanceStory(data?.verdict.story, {
        price: shownPrice,
        alertState: storyAlert?.state ?? null,
      }),
    [data?.verdict.story, shownPrice, storyAlert?.state],
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

      <div className="tg-watchbar">
        <div className="tg-watchbar-head">
          <strong>Watch tabs</strong>
          <span>
            One pair is monitored live at a time. The ledger below follows the quote you have selected.
          </span>
        </div>
        <div className="tg-watchbar-actions">
          <button className="tg-close-btn" onClick={pinCurrentPair} disabled={!pair || isWatched}>
            {isWatched ? 'Pinned' : 'Pin current pair'}
          </button>
          <button
            className="tg-take-btn"
            onClick={() => void activateMonitorPair(pair)}
            disabled={!pair || monitor?.pair === pair}
          >
            {monitor?.pair === pair ? 'Live monitor active' : 'Monitor this pair'}
          </button>
        </div>
        <div className="tg-watch-tabs">
          {watchedPairs.map(pairId => {
            const market = health?.pairs.find(p => p.id === pairId)
            const monitored = monitor?.pair === pairId
            const selected = pair === pairId
            return (
              <div
                key={pairId}
                className={`tg-watch-tab ${selected ? 'tg-watch-tab--on' : ''} ${monitored ? 'tg-watch-tab--live' : ''}`}
              >
                <button className="tg-watch-tab-main" onClick={() => selectWatchedPair(pairId)}>
                  <span>{market?.display ?? pairId.toUpperCase()}</span>
                  <span className="tg-watch-tab-meta">
                    {monitored ? 'monitoring live' : 'ledger only'}
                  </span>
                </button>
                {watchedPairs.length > 1 && (
                  <button className="tg-watch-tab-x" onClick={() => unpinPair(pairId)} aria-label={`Remove ${pairId}`}>
                    ×
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </div>

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
          <span className="tg-bias-meta">Execution path appears only for ENTER-ready or live positions</span>
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
                historyTrade={historyTrade && historyTrade.pair === data.pair ? historyTrade : null}
                liveTrades={liveTradesForPair}
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
                    onTake={handleTake}
                    taking={taking}
                    compact
                    livePrice={shownPrice}
                    alertState={storyAlert?.state ?? null}
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
        </>
      ) : (
        !error && <div className="tg-loading">{booting ? 'Loading market config…' : 'Loading scan…'}</div>
      )}

      {tradeMsg && <div className="tg-trade-msg">{tradeMsg}</div>}

        <PositionsPanel
          monitor={monitor}
        alerts={alerts}
        pagination={alertsPage}
          stats={stats}
          currentPlan={data?.verdict.plan ? { ...data.verdict.plan, generated_at: data.generated_at, rescan: data.rescan } : null}
        aiAnalysis={aiAnalysis}
        aiLoading={aiLoading}
        onRunAiAnalysis={() => void loadAiAnalysis(true)}
        selectedHistoryAlertId={historyTrade?.alert_id ?? null}
        onClose={handleClose}
        onShowHistory={handleShowHistory}
        onPage={handleAlertsPage}
        range={ledgerRange}
        onRange={handleLedgerRange}
        closingId={closingId}
        />

      <footer className="tg-foot">
        Analysis and education only — not financial advice. TradeGuard never holds funds or
        trades on your behalf.
      </footer>

      <Clippy story={liveStory} />
    </div>
  )
}
