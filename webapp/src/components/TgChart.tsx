import { useEffect, useRef } from 'react'
import {
  createChart,
  CrosshairMode,
  TickMarkType,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import type { AlertRow, ClosedTradeRow, LayerKey, ScanResult, Zone } from '../api/types'

// The whole app displays East Africa Time (UTC+3), matching the closed-ledger and
// end-of-day panels. lightweight-charts renders times in UTC by default, so the
// axis and crosshair used to disagree with every other timestamp by 3 hours (and
// flip the date near midnight). These helpers keep the chart on EAT everywhere.
const EAT_OFFSET_SECONDS = 3 * 3600
function eatParts(sec: number) {
  const d = new Date((sec + EAT_OFFSET_SECONDS) * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return {
    y: d.getUTCFullYear(),
    mo: p(d.getUTCMonth() + 1),
    d: p(d.getUTCDate()),
    h: p(d.getUTCHours()),
    mi: p(d.getUTCMinutes()),
    mon: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()],
  }
}
function eatStamp(sec: number) {
  const t = eatParts(sec)
  return `${t.y}-${t.mo}-${t.d} ${t.h}:${t.mi} EAT`
}
const _TF_SECONDS: Record<string, number> = {
  '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1H': 3600, '4H': 14400, '1D': 86400, '1W': 604800,
}
function tfSeconds(tf?: string | null): number {
  return (tf && _TF_SECONDS[tf]) || 300
}
function eatTick(sec: number, tickMarkType: TickMarkType) {
  const t = eatParts(sec)
  if (tickMarkType === TickMarkType.Year) return `${t.y}`
  if (tickMarkType === TickMarkType.Month) return `${t.mon} ${t.y}`
  if (tickMarkType === TickMarkType.DayOfMonth) return `${t.mon} ${t.d}`
  return `${t.h}:${t.mi}` // Time / TimeWithSeconds
}

// lightweight-charts v4 API. Do NOT upgrade to v5 without rewriting this file:
// v5 replaces addCandlestickSeries()/addLineSeries() with
// chart.addSeries(CandlestickSeries, ...). See PLAN.md known traps.

interface Props {
  data: ScanResult
  futureView: 'projected' | 'execution' | 'both'
  srFocus: boolean
  historyTrade: ClosedTradeRow | null
  liveTrades: AlertRow[]
  layers: Record<LayerKey, boolean>
  priorityFilters: {
    sr: 'all' | 'high' | 'medium' | 'low'
    supply_demand: 'all' | 'high' | 'medium' | 'low'
    fvg: 'all' | 'high' | 'medium' | 'low'
  }
  checkedPatterns: Record<string, boolean>
  showPending: boolean
  checkedChartPatterns: Record<string, boolean>
  showFormingCharts: boolean
}

// category → rgba colour (alpha varies by confirmed/forming)
function chartPatternColor(category: string, alpha: number) {
  if (category === 'bullish') return `rgba(29,158,117,${alpha})`
  if (category === 'bearish') return `rgba(226,75,74,${alpha})`
  return `rgba(100,116,139,${alpha})`
}

// A shaded rectangle on the overlay. fromTime/toTime clamp its x-extent to the
// bars it belongs to; without them it spans the full width (used for the range
// channel, whose flat boundaries are legitimately horizontal).
interface Band {
  top: number
  bottom: number
  fill: string
  border: string
  label?: string
  labelColor?: string
  fromTime?: number
  toTime?: number
  boxed?: boolean // draw a full 4-sided rectangle (vs. just top/bottom edges)
  borderStyle?: string
}

function historySide(trade: ClosedTradeRow): 'long' | 'short' {
  if (trade.side === 'long' || trade.side === 'short') return trade.side
  return trade.target >= trade.entry ? 'long' : 'short'
}

function patternMarker(p: ScanResult['layers']['patterns_all'][number]) {
  if (!p.confirmed) {
    return { shape: 'circle' as const, color: '#94a3b8' }
  }
  if (p.group === 'continuation_momentum') {
    return { shape: 'square' as const, color: '#2563eb' }
  }
  if (p.group === 'exhaustion_indecision') {
    return { shape: 'square' as const, color: '#d97706' }
  }
  const bull = p.direction === 'bullish'
  return {
    shape: bull ? ('arrowUp' as const) : ('arrowDown' as const),
    color: bull ? '#1d9e75' : '#e24b4a',
  }
}

function replayResultColor(trade: ClosedTradeRow) {
  if (trade.result_r > 0) return '#1D9E75'
  if (trade.result_r < 0) return '#E24B4A'
  return '#64748b'
}

// Moving-average name -> toggle key + colour. Order matches the engine MA_CONFIG.
const MA_KEY: Record<string, LayerKey> = {
  'EMA 9': 'ema9',
  'EMA 21': 'ema21',
  'EMA 50': 'ema50',
  'SMA 200': 'sma200',
}
const MA_COLOR: Record<string, string> = {
  'EMA 9': '#3b82f6',
  'EMA 21': '#a855f7',
  'EMA 50': '#f59e0b',
  'SMA 200': '#64748b',
}

function keepZone(z: Zone, filter: 'all' | 'high' | 'medium' | 'low') {
  return filter === 'all' ? true : z.priority === filter
}

function selectSrZones(
  zones: Zone[],
  filter: 'all' | 'high' | 'medium' | 'low',
  currentPrice: number,
  limit = 4,
) {
  return zones
    .filter(z => keepZone(z, filter))
    .map(z => ({
      ...z,
      distance: Math.abs(z.level_mid - currentPrice),
      touchesScore: z.touches ?? 1,
    }))
    .sort((a, b) => {
      if (b.priority_score !== a.priority_score) return b.priority_score - a.priority_score
      if (a.distance !== b.distance) return a.distance - b.distance
      return (b.touchesScore ?? 0) - (a.touchesScore ?? 0)
    })
    .slice(0, limit)
}

export default function TgChart({
  data,
  futureView,
  srFocus,
  historyTrade,
  liveTrades,
  layers,
  priorityFilters,
  checkedPatterns,
  showPending,
  checkedChartPatterns,
  showFormingCharts,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const linesRef = useRef<ISeriesApi<'Line'>[]>([])
  const priceLinesRef = useRef<IPriceLine[]>([])
  const bandsRef = useRef<Band[]>([])
  const futureFromRef = useRef<number | null>(null)
  const focusedHistoryIdRef = useRef<number | null>(null)

  function clearLineSeries(chart: IChartApi) {
    for (const series of linesRef.current) {
      try {
        chart.removeSeries(series)
      } catch {
        // React Strict Mode remounts can leave stale series refs from a
        // previously-destroyed chart instance. Ignore and rebuild.
      }
    }
    linesRef.current = []
  }

  function clearPriceLines() {
    const candles = candlesRef.current
    if (!candles) return
    for (const pl of priceLinesRef.current) {
      try {
        candles.removePriceLine(pl)
      } catch {
        // stale ref after remount — ignore
      }
    }
    priceLinesRef.current = []
  }

  // --- create the chart once -------------------------------------------------
  useEffect(() => {
    if (!hostRef.current) return
    const chart = createChart(hostRef.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#64748b', fontSize: 11 },
      grid: {
        vertLines: { color: 'rgba(148,163,184,.12)' },
        horzLines: { color: 'rgba(148,163,184,.12)' },
      },
      rightPriceScale: { borderColor: 'rgba(148,163,184,.25)' },
      timeScale: {
        borderColor: 'rgba(148,163,184,.25)',
        timeVisible: true,
        rightOffset: 6,
        // axis ticks in EAT so they match the ledger/EOD timestamps
        tickMarkFormatter: (time: number, tickMarkType: TickMarkType) => eatTick(time, tickMarkType),
      },
      // crosshair vertical-axis label in EAT
      localization: { timeFormatter: (time: number) => eatStamp(time) },
      // Normal (not Magnet) crosshair so the readout tracks the pointer exactly.
      crosshair: { mode: CrosshairMode.Normal },
      // A vertical touch-drag on the chart should scroll the PAGE, not pan the chart —
      // otherwise touching the chart while scrolling to the bottom hijacks the gesture.
      // Horizontal drag + pinch still navigate chart history.
      handleScroll: { vertTouchDrag: false },
    })
    chartRef.current = chart
    candlesRef.current = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    })

    const redraw = () => drawOverlay()
    chart.timeScale().subscribeVisibleTimeRangeChange(redraw)
    chart.subscribeCrosshairMove(handleCrosshair)
    const ro = new ResizeObserver(redraw)
    ro.observe(hostRef.current)

    return () => {
      ro.disconnect()
      chart.unsubscribeCrosshairMove(handleCrosshair)
      clearPriceLines()
      clearLineSeries(chart)
      bandsRef.current = []
      futureFromRef.current = null
      if (overlayRef.current) overlayRef.current.innerHTML = ''
      chart.remove()
      chartRef.current = null
      candlesRef.current = null
    }
  }, [])

  // --- push data + layers ----------------------------------------------------
  useEffect(() => {
    const chart = chartRef.current
    const candles = candlesRef.current
    if (!chart || !candles) return

    candles.setData(
      data.candles.map(c => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    )

    clearLineSeries(chart)
    clearPriceLines()

    // --- moving averages (EMA 9/21/50 + SMA 200) as line series ---
    data.layers.moving_averages.forEach(ser => {
      const key = MA_KEY[ser.name]
      if (key && !layers[key]) return
      if (!ser.points.length) return
      const s = chart.addLineSeries({
        color: MA_COLOR[ser.name] ?? '#94a3b8',
        lineWidth: ser.kind === 'sma' ? 2 : 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      s.setData(ser.points.map(p => ({ time: p.time as UTCTimestamp, value: p.value })))
      linesRef.current.push(s)
    })

    // --- supply/demand + FVG as time-clamped overlay bands ---
    const bands: Band[] = []
    const lastCandleTime = data.candles.length
      ? data.candles[data.candles.length - 1].time
      : undefined
    const stepSeconds =
      data.candles.length >= 2
        ? data.candles[data.candles.length - 1].time - data.candles[data.candles.length - 2].time
        : 0
    if (layers.sr) {
      selectSrZones(data.layers.sr, priorityFilters.sr, data.price).forEach(z => {
        const support = z.kind === 'support'
        bands.push({
          top: z.level_mid,
          bottom: z.level_mid,
          fill: 'transparent',
          border: support ? 'rgba(2,132,199,.92)' : 'rgba(234,88,12,.92)',
          label: `${support ? 'support' : 'resistance'} x${z.touches ?? '?'} · ${z.priority}`,
          labelColor: support ? '#0369a1' : '#c2410c',
          borderStyle: support
            ? '3px solid rgba(2,132,199,.92)'
            : '3px solid rgba(234,88,12,.92)',
        })
      })
    }

    if (layers.supply_demand && !srFocus) {
      data.layers.supply_demand
        .filter(z => keepZone(z, priorityFilters.supply_demand))
        .forEach(z => {
          const demand = z.kind === 'demand'
          bands.push({
            top: z.level_max,
            bottom: z.level_min,
            fill: demand ? 'rgba(16,185,129,.16)' : 'rgba(244,63,94,.14)',
            border: demand ? 'rgba(16,185,129,.4)' : 'rgba(244,63,94,.4)',
            label: `${z.kind}${z.fresh ? ' · fresh' : ''}`,
            labelColor: demand ? '#059669' : '#e11d48',
            boxed: true,
            // formation -> present: a zone lives from where it formed until now
            fromTime: z.time,
            toTime: lastCandleTime,
          })
        })
    }
    if (layers.fvg && !srFocus) {
      data.layers.fvg.filter(z => keepZone(z, priorityFilters.fvg)).forEach(z => {
        bands.push({
          top: z.top ?? z.level_max,
          bottom: z.bottom ?? z.level_min,
          fill: z.filled ? 'rgba(168,85,247,.14)' : 'rgba(168,85,247,.30)',
          border: z.filled ? 'rgba(147,51,234,.6)' : 'rgba(147,51,234,1)',
          // no text label — a boxed rectangle carries the meaning; labels congest
          boxed: true,
          // anchor at the gap's formation and extend FORWARD toward the fill:
          //  - filled  -> box ends at the bar that filled it
          //  - unfilled -> box runs ahead to the right edge (expected fill zone)
          fromTime: z.start_time ?? z.time,
          toTime: z.filled ? z.fill_time ?? lastCandleTime : undefined,
        })
      })
    }

    const plan = data.verdict?.plan
    const actionablePlan =
      plan?.status === 'ENTER_READY' &&
      plan.entry != null &&
      plan.stop != null &&
      plan.target != null &&
      (plan.side === 'long' || plan.side === 'short')
        ? plan
        : null
    const activeLiveTrades = liveTrades.filter(
      t => t.entry != null && t.stop != null && t.target != null && (t.side === 'long' || t.side === 'short'),
    )

    const addExecutionBands = (args: {
      side: 'long' | 'short'
      entry: number
      stop: number
      target: number
      fromTime?: number
      toTime?: number
      targetLabel?: string
      altTargets?: { price: number; label?: string }[]
    }) => {
      const { side, entry, stop, target, fromTime, toTime, targetLabel, altTargets = [] } = args
      const isLong = side === 'long'
      bands.push({
        top: isLong ? entry : stop,
        bottom: isLong ? stop : entry,
        fill: 'rgba(239,68,68,.18)',
        border: 'rgba(220,38,38,.7)',
        boxed: true,
        fromTime,
        toTime,
      })
      bands.push({
        top: isLong ? target : entry,
        bottom: isLong ? entry : target,
        fill: 'rgba(16,185,129,.18)',
        border: 'rgba(5,150,105,.7)',
        boxed: true,
        fromTime,
        toTime,
        label: targetLabel,
        labelColor: '#047857',
      })
      bands.push({
        top: entry,
        bottom: entry,
        fill: 'transparent',
        border: 'rgba(15,23,42,.72)',
        fromTime,
        toTime,
        label: `${side} entry`,
        labelColor: '#0f172a',
        borderStyle: '1.5px solid rgba(15,23,42,.72)',
      })
      altTargets.forEach((alt, i) => {
        bands.push({
          top: alt.price,
          bottom: alt.price,
          fill: 'transparent',
          border: 'rgba(14,165,233,.55)',
          fromTime,
          toTime,
          label: i === 0 ? (alt.label ?? 'alt TP') : undefined,
          labelColor: '#0369a1',
          borderStyle: '1px dashed rgba(14,165,233,.75)',
        })
      })
    }

    if (!srFocus && futureView !== 'projected') {
      if (activeLiveTrades.length > 0) {
        activeLiveTrades.forEach(trade => {
          addExecutionBands({
            side: trade.side,
            entry: trade.entry,
            stop: trade.stop,
            target: trade.target,
            fromTime: trade.armed_at,
            toTime: lastCandleTime,
          })
        })
      } else if (actionablePlan) {
        const fromTime = lastCandleTime
        const toTime = lastCandleTime != null && stepSeconds > 0 ? lastCandleTime + stepSeconds * 6 : undefined
        const planSide = actionablePlan.side as 'long' | 'short'
        const planEntry = actionablePlan.entry as number
        const planStop = actionablePlan.stop as number
        const planTarget = actionablePlan.target as number
        addExecutionBands({
          side: planSide,
          entry: planEntry,
          stop: planStop,
          target: planTarget,
          fromTime,
          toTime,
          targetLabel: actionablePlan.target_basis ?? undefined,
          altTargets: actionablePlan.alternatives.map((alt, i) => ({
            price: alt.price,
            label: i === 0 ? 'alt TP' : undefined,
          })),
        })
      }
    }

    // --- market-structure RANGE boundaries (legit full-width horizontal channel) ---
    const stc = data.structure
    if (stc?.state === 'RANGE' && stc.range_high != null && stc.range_low != null) {
      bands.push({
        top: stc.range_high,
        bottom: stc.range_low,
        fill: 'rgba(100,116,139,.08)',
        border: 'rgba(100,116,139,.55)',
        label: `range ${stc.range_low.toFixed(2)}–${stc.range_high.toFixed(2)}`,
        labelColor: '#475569',
      })
    }

    // --- chart / structure patterns (multi-bar): trendlines, regions, targets ---
    const shownChartPatterns = (data.layers.chart_patterns ?? []).filter(
      cp => checkedChartPatterns[cp.name] && (cp.status === 'confirmed' || showFormingCharts),
    )
    shownChartPatterns.forEach(cp => {
      const confirmed = cp.status === 'confirmed'
      const alpha = confirmed ? 0.95 : 0.5
      cp.lines.forEach(ln => {
        if (ln.t0 == null || ln.t1 == null || ln.t0 === ln.t1) return
        const asc = ln.t0 <= ln.t1
        const s = chart.addLineSeries({
          color: chartPatternColor(cp.category, alpha),
          lineWidth: confirmed ? 2 : 1,
          lineStyle: ln.kind === 'neckline' ? 2 : confirmed ? 0 : 2, // dashed neckline / forming
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        })
        s.setData([
          { time: (asc ? ln.t0 : ln.t1) as UTCTimestamp, value: asc ? ln.y0 : ln.y1 },
          { time: (asc ? ln.t1 : ln.t0) as UTCTimestamp, value: asc ? ln.y1 : ln.y0 },
        ])
        linesRef.current.push(s)
      })
      if (cp.region) {
        bands.push({
          top: cp.region.top,
          bottom: cp.region.bottom,
          fill: chartPatternColor(cp.category, 0.08),
          border: chartPatternColor(cp.category, alpha),
          boxed: true,
          fromTime: cp.region.start_time,
          toTime: cp.region.end_time,
        })
      }
      if (confirmed && cp.target != null) {
        priceLinesRef.current.push(
          candles.createPriceLine({
            price: cp.target,
            color: chartPatternColor(cp.category, 0.9),
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: `${cp.name} target`,
          }),
        )
      }
    })

    bandsRef.current = bands

    // --- markers: confirmed (or pending) patterns from the checklist + entries ---
    const markers: Parameters<typeof candles.setMarkers>[0] = []
    data.layers.patterns_all.forEach(p => {
      if (!p.available) return
      if (!checkedPatterns[p.name]) return
      const confirmed = p.confirmed
      if (!confirmed && !showPending) return
      const marker = patternMarker(p)
      markers.push({
        time: p.time as UTCTimestamp,
        position: p.direction === 'bullish' ? 'belowBar' : 'aboveBar',
        color: marker.color,
        shape: marker.shape,
        text: confirmed ? p.name : `${p.name} (pending)`,
      })
    })
    if (!srFocus && futureView !== 'execution') {
      data.future.entries.forEach(e => {
        markers.push({
          time: e.t_future as UTCTimestamp,
          position: e.side === 'long' ? 'belowBar' : 'aboveBar',
          color: e.side === 'long' ? '#15803d' : '#b91c1c',
          shape: e.side === 'long' ? 'arrowUp' : 'arrowDown',
          text: `projected ${e.side} · ~${e.eta_bars} bars · x${e.confluence}`,
        })
      })
    }
    // chart-pattern pivots (dots) + one name label at the first pivot
    shownChartPatterns.forEach(cp => {
      const color = chartPatternColor(cp.category, cp.status === 'confirmed' ? 1 : 0.6)
      cp.pivots.forEach((pv, i) => {
        markers.push({
          time: pv.time as UTCTimestamp,
          position: cp.category === 'bearish' ? 'aboveBar' : 'belowBar',
          color,
          shape: 'circle',
          text: i === 0 ? `${cp.name}${cp.status === 'forming' ? ' (forming)' : ''}` : pv.label,
        })
      })
    })
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    candles.setMarkers(markers)

    futureFromRef.current = lastCandleTime ?? null

    if (futureFromRef.current === null) {
      chart.timeScale().fitContent()
    }
    requestAnimationFrame(drawOverlay)
  }, [data, futureView, srFocus, historyTrade, liveTrades, layers, priorityFilters, checkedPatterns, showPending, checkedChartPatterns, showFormingCharts])

  useEffect(() => {
    const chart = chartRef.current
    const ts = chart?.timeScale()
    if (!chart || !ts || !historyTrade || historyTrade.replay_mode === 'unavailable' || !historyTrade.entry_time || !historyTrade.exit_time) {
      focusedHistoryIdRef.current = null
      return
    }
    if (focusedHistoryIdRef.current === historyTrade.alert_id) return
    // Frame the trade with real context: ~30 bars before entry and ~15 after exit,
    // so a one-bar scalp isn't blown up to fill the screen ("too compact").
    const barSec = tfSeconds(historyTrade.timeframe)
    ts.setVisibleRange({
      from: (historyTrade.entry_time - 30 * barSec) as UTCTimestamp,
      to: (historyTrade.exit_time + 15 * barSec) as UTCTimestamp,
    })
    focusedHistoryIdRef.current = historyTrade.alert_id
    requestAnimationFrame(drawOverlay)
  }, [historyTrade])

  // --- crosshair tooltip: exact O/H/L/C at the cursor ------------------------
  function handleCrosshair(param: Parameters<Parameters<IChartApi['subscribeCrosshairMove']>[0]>[0]) {
    const tip = tooltipRef.current
    const candles = candlesRef.current
    if (!tip || !candles) return
    if (!param.time || !param.point) {
      tip.style.display = 'none'
      return
    }
    const bar = param.seriesData.get(candles) as
      | { open: number; high: number; low: number; close: number }
      | undefined
    if (!bar) {
      tip.style.display = 'none'
      return
    }
    const up = bar.close >= bar.open
    const stamp = eatStamp(param.time as number)
    tip.innerHTML =
      `<span class="tg-tt-time">${stamp}</span>` +
      `<span>O <b>${bar.open.toFixed(2)}</b></span>` +
      `<span>H <b>${bar.high.toFixed(2)}</b></span>` +
      `<span>L <b>${bar.low.toFixed(2)}</b></span>` +
      `<span class="${up ? 'tg-green' : 'tg-red'}">C <b>${bar.close.toFixed(2)}</b></span>`
    tip.style.display = 'flex'
  }

  // --- overlay: shaded zone bands + future region ---------------------------
  function drawOverlay() {
    const chart = chartRef.current
    const candles = candlesRef.current
    const el = overlayRef.current
    if (!chart || !candles || !el) return

    el.innerHTML = ''
    const width = el.clientWidth
    const ts = chart.timeScale()

    // shaded "future" region, right of the last real candle
    if (futureFromRef.current !== null) {
      const x = ts.timeToCoordinate(futureFromRef.current as UTCTimestamp)
      if (x !== null) {
        const div = document.createElement('div')
        div.className = 'tg-future'
        div.style.left = `${x}px`
        div.style.width = `${Math.max(0, width - x)}px`
        el.appendChild(div)
      }
    }

    bandsRef.current.forEach(b => {
      const yTop = candles.priceToCoordinate(b.top)
      const yBottom = candles.priceToCoordinate(b.bottom)
      if (yTop === null || yBottom === null) return
      const h = Math.max(2, yBottom - yTop)

      // x-extent: clamp to the band's bars when it carries a time span
      let left = 0
      let boxWidth = width
      if (b.fromTime !== undefined || b.toTime !== undefined) {
        const xa = b.fromTime !== undefined ? ts.timeToCoordinate(b.fromTime as UTCTimestamp) : null
        const xb = b.toTime !== undefined ? ts.timeToCoordinate(b.toTime as UTCTimestamp) : null
        if (xa === null && xb === null) return // wholly off-screen
        const l = xa === null ? 0 : xa
        const r = xb === null ? width : xb
        left = Math.max(0, Math.min(l, r))
        // enforce a visible minimum so a 3-candle gap doesn't render as a hairline
        boxWidth = Math.max(b.boxed ? 12 : 2, Math.abs(r - l))
      }

      const div = document.createElement('div')
      div.className = 'tg-band'
      div.style.left = `${left}px`
      div.style.width = `${boxWidth}px`
      div.style.top = `${yTop}px`
      div.style.height = `${h}px`
      div.style.background = b.fill
      if (b.boxed) {
        // a full rectangle around the zone/gap — reads as a box, no text needed
        div.style.border = `1.5px solid ${b.border}`
        div.style.borderRadius = '2px'
      } else {
        const line = b.borderStyle ?? `1px solid ${b.border}`
        div.style.borderTop = line
        div.style.borderBottom = line
      }
      if (b.label) {
        const tag = document.createElement('span')
        tag.className = 'tg-band-label'
        tag.textContent = b.label
        tag.style.color = b.labelColor ?? '#64748b'
        div.appendChild(tag)
      }
      el.appendChild(div)
    })

    if (historyTrade?.replay_mode !== 'unavailable' && historyTrade?.entry_time && historyTrade?.exit_time && historyTrade.entry != null && historyTrade.exit_price != null) {
      const svgNs = 'http://www.w3.org/2000/svg'
      const svg = document.createElementNS(svgNs, 'svg')
      svg.setAttribute('class', 'tg-history-svg')
      svg.setAttribute('width', String(width))
      svg.setAttribute('height', String(el.clientHeight))
      svg.setAttribute('viewBox', `0 0 ${width} ${el.clientHeight}`)

      const times = data.candles.map(c => c.time)
      const floorTime = (epoch: number) => {
        let snapped = times[0] ?? epoch
        for (const t of times) {
          if (t > epoch) break
          snapped = t
        }
        return snapped
      }
      const visible = ts.getVisibleRange()
      const resolveX = (epoch: number) => {
        const snapped = floorTime(epoch)
        const coord = ts.timeToCoordinate(snapped as UTCTimestamp)
        if (coord !== null) return { x: coord, clipped: false }
        if (!visible) return null
        if (snapped < (visible.from as number)) return { x: 0, clipped: true }
        if (snapped > (visible.to as number)) return { x: width, clipped: true }
        return null
      }

      const p1 = resolveX(historyTrade.entry_time)
      const p2 = resolveX(historyTrade.exit_time)
      // entry marker at the ACTUAL fill price when we have it, else the planned entry
      const entryPrice = historyTrade.fill_price ?? historyTrade.entry
      const y1 = candles.priceToCoordinate(entryPrice)
      const y2 = candles.priceToCoordinate(historyTrade.exit_price)
      if (p1 && p2 && y1 !== null && y2 !== null) {
        const exact = historyTrade.replay_mode === 'exact'
        const side = historySide(historyTrade)
        const entryColor = side === 'long' ? '#1D9E75' : '#E24B4A'
        const resultColor = replayResultColor(historyTrade)

        const line = document.createElementNS(svgNs, 'line')
        line.setAttribute('x1', `${p1.x}`)
        line.setAttribute('y1', `${y1}`)
        line.setAttribute('x2', `${p2.x}`)
        line.setAttribute('y2', `${y2}`)
        line.setAttribute('stroke', exact ? resultColor : '#64748b')
        line.setAttribute('stroke-width', exact ? '2' : '1.5')
        line.setAttribute('stroke-dasharray', exact ? '6 4' : '2 4')
        line.setAttribute('opacity', exact ? '0.7' : '0.9')
        svg.appendChild(line)

        const entryTri = document.createElementNS(svgNs, 'polygon')
        const up = side === 'long'
        const triPoints = up
          ? `${p1.x},${y1 - 8} ${p1.x - 7},${y1 + 4} ${p1.x + 7},${y1 + 4}`
          : `${p1.x},${y1 + 8} ${p1.x - 7},${y1 - 4} ${p1.x + 7},${y1 - 4}`
        entryTri.setAttribute('points', triPoints)
        entryTri.setAttribute('fill', entryColor)
        entryTri.setAttribute('stroke', '#ffffff')
        entryTri.setAttribute('stroke-width', '1.5')
        entryTri.setAttribute('opacity', p1.clipped ? '0.45' : '1')
        svg.appendChild(entryTri)

        const exitDiamond = document.createElementNS(svgNs, 'rect')
        exitDiamond.setAttribute('x', `${p2.x - 5}`)
        exitDiamond.setAttribute('y', `${y2 - 5}`)
        exitDiamond.setAttribute('width', '10')
        exitDiamond.setAttribute('height', '10')
        exitDiamond.setAttribute('fill', resultColor)
        exitDiamond.setAttribute('stroke', '#ffffff')
        exitDiamond.setAttribute('stroke-width', '1.5')
        exitDiamond.setAttribute('transform', `rotate(45 ${p2.x} ${y2})`)
        exitDiamond.setAttribute('opacity', p2.clipped ? '0.45' : '1')
        svg.appendChild(exitDiamond)

        // --- expected levels: the planned stop (risk) and target (reward) the trade
        // was set up with, so the chart shows what was meant to happen vs what did.
        const levelLine = (price: number, color: string, label: string) => {
          const y = candles.priceToCoordinate(price)
          if (y === null) return
          const x0 = Math.max(0, Math.min(p1.x, p2.x) - 4)
          const ln = document.createElementNS(svgNs, 'line')
          ln.setAttribute('x1', `${x0}`); ln.setAttribute('y1', `${y}`)
          ln.setAttribute('x2', `${width}`); ln.setAttribute('y2', `${y}`)
          ln.setAttribute('stroke', color)
          ln.setAttribute('stroke-width', '1')
          ln.setAttribute('stroke-dasharray', '4 4')
          ln.setAttribute('opacity', '0.55')
          svg.appendChild(ln)
          const tag = document.createElementNS(svgNs, 'text')
          tag.setAttribute('x', `${width - 6}`); tag.setAttribute('y', `${y - 3}`)
          tag.setAttribute('text-anchor', 'end')
          tag.setAttribute('font-size', '10'); tag.setAttribute('fill', color)
          tag.textContent = `${label} ${price.toFixed(2)}`
          svg.appendChild(tag)
        }
        if (Number.isFinite(historyTrade.target)) levelLine(historyTrade.target, '#15803d', 'target')
        if (Number.isFinite(historyTrade.stop)) levelLine(historyTrade.stop, '#b91c1c', 'stop')

        const chipText = `${exact ? '' : '≈ '}${side.toUpperCase()} ${historyTrade.result_r >= 0 ? '+' : ''}${historyTrade.result_r}R · ${historyTrade.replay_mode}`
        const chipWidth = Math.max(120, chipText.length * 6.4)
        const chipX = Math.max(8, Math.min(width - chipWidth - 8, p2.x + 10))
        const chipY = Math.max(18, y2 - 24)
        const chip = document.createElementNS(svgNs, 'rect')
        chip.setAttribute('x', `${chipX}`)
        chip.setAttribute('y', `${chipY - 14}`)
        chip.setAttribute('width', `${chipWidth}`)
        chip.setAttribute('height', '22')
        chip.setAttribute('rx', '11')
        chip.setAttribute('fill', 'rgba(255,255,255,.95)')
        chip.setAttribute('stroke', exact ? resultColor : '#94a3b8')
        chip.setAttribute('stroke-width', '1')
        svg.appendChild(chip)

        const chipLabel = document.createElementNS(svgNs, 'text')
        chipLabel.setAttribute('x', `${chipX + 10}`)
        chipLabel.setAttribute('y', `${chipY}`)
        chipLabel.setAttribute('fill', exact ? '#0f172a' : '#475569')
        chipLabel.setAttribute('font-size', '11')
        chipLabel.setAttribute('font-weight', '700')
        chipLabel.textContent = chipText
        svg.appendChild(chipLabel)
      }

      el.appendChild(svg)
    }
  }

  return (
    <div className="tg-chart-wrap">
      <div ref={hostRef} className="tg-chart" />
      <div ref={overlayRef} className="tg-overlay" />
      <div ref={tooltipRef} className="tg-tooltip" style={{ display: 'none' }} />
    </div>
  )
}
