import type {
  AlertRow,
  Candle,
  ChecklistItem,
  ChecklistStatus,
  ReconnaissanceLiquidityLevel,
  Story,
} from '../api/types'

export type LiquidityView = 'nearby' | 'all' | 'off'
export type DecisionEventKind = 'touch' | 'sweep' | 'reclaim'

export interface DecisionEvent {
  time: number
  price: number
  kind: DecisionEventKind
  source: 'alert' | 'candles'
}

export function checklistStatus(item: ChecklistItem): ChecklistStatus {
  return item.status ?? (item.passed ? 'passed' : 'failed')
}

export function selectLiquidity(
  levels: ReconnaissanceLiquidityLevel[],
  price: number | null,
  view: LiquidityView,
) {
  const valid = levels.filter(level => Number.isFinite(level.price))
  if (view === 'off') return []
  if (view === 'all' || price == null || !Number.isFinite(price)) {
    return [...valid].sort((a, b) => b.price - a.price)
  }
  const above = valid
    .filter(level => level.price >= price)
    .sort((a, b) => a.price - b.price)[0]
  const below = valid
    .filter(level => level.price < price)
    .sort((a, b) => b.price - a.price)[0]
  return [above, below].filter((level): level is ReconnaissanceLiquidityLevel => Boolean(level))
}

const ACTIVE_ALERT_STATES = new Set(['WATCHING', 'TOUCHED', 'SWEPT', 'FILLED'])

function zonesOverlap(alert: AlertRow, zoneMin?: number | null, zoneMax?: number | null) {
  if (zoneMin == null || zoneMax == null) return false
  return alert.zone_min <= zoneMax && zoneMin <= alert.zone_max
}

export function selectFocusedAlert(
  alerts: AlertRow[],
  plan: { side?: string | null; zone_min?: number | null; zone_max?: number | null; entry_tf?: string | null } | null,
  selectedTimeframe: string,
  firstVisibleTime?: number | null,
) {
  const eligible = alerts.filter(alert =>
    alert.events.some(event => firstVisibleTime == null || event.ts >= firstVisibleTime) ||
    ACTIVE_ALERT_STATES.has(alert.state),
  )
  return eligible
    .map(alert => {
      let score = ACTIVE_ALERT_STATES.has(alert.state) ? 100 : 0
      if (plan?.side && alert.side === plan.side) score += 30
      if (zonesOverlap(alert, plan?.zone_min, plan?.zone_max)) score += 30
      if (alert.timeframe === (plan?.entry_tf || selectedTimeframe)) score += 10
      return { alert, score }
    })
    .sort((a, b) => b.score - a.score || b.alert.updated_at - a.alert.updated_at)[0]?.alert ?? null
}

export function alertDecisionEvents(alert: AlertRow | null, firstVisibleTime?: number | null): DecisionEvent[] {
  if (!alert) return []
  const kindByState: Record<string, DecisionEventKind | undefined> = {
    TOUCHED: 'touch',
    SWEPT: 'sweep',
    FILLED: 'reclaim',
  }
  return alert.events.flatMap(event => {
    const kind = kindByState[event.state]
    if (!kind || (firstVisibleTime != null && event.ts < firstVisibleTime)) return []
    return [{ time: event.ts, price: event.price, kind, source: 'alert' as const }]
  })
}

export function inferDecisionEvents(
  candles: Candle[],
  story: Story | null | undefined,
  plan: { side?: string | null; zone_min?: number | null; zone_max?: number | null } | null,
): DecisionEvent[] {
  const side = plan?.side
  const zoneMin = plan?.zone_min
  const zoneMax = plan?.zone_max
  const sweepBeat = story?.beats.find(beat => beat.kind === 'sweep' && beat.level != null)
  if ((side !== 'long' && side !== 'short') || zoneMin == null || zoneMax == null || sweepBeat?.level == null) {
    return []
  }

  const window = candles.slice(-60)
  const touchedAt = window.findIndex(candle => candle.low <= zoneMax && candle.high >= zoneMin)
  if (touchedAt < 0) return []
  const touched = window[touchedAt]
  const events: DecisionEvent[] = [
    { time: touched.time, price: side === 'long' ? touched.low : touched.high, kind: 'touch', source: 'candles' },
  ]

  const afterTouch = window.slice(touchedAt)
  const sweptAt = afterTouch.findIndex(candle =>
    side === 'long' ? candle.low <= sweepBeat.level! : candle.high >= sweepBeat.level!,
  )
  if (sweptAt < 0) return events
  const swept = afterTouch[sweptAt]
  events.push({
    time: swept.time,
    price: side === 'long' ? swept.low : swept.high,
    kind: 'sweep',
    source: 'candles',
  })

  const afterSweep = afterTouch.slice(sweptAt)
  const reclaimed = afterSweep.find(candle => side === 'long' ? candle.close >= zoneMin : candle.close <= zoneMax)
  if (reclaimed) {
    events.push({ time: reclaimed.time, price: reclaimed.close, kind: 'reclaim', source: 'candles' })
  }
  return events
}
