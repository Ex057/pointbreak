import { describe, expect, it } from 'vitest'
import type { AlertRow, Candle, Story } from '../api/types'
import {
  alertDecisionEvents,
  checklistStatus,
  inferDecisionEvents,
  selectFocusedAlert,
  selectLiquidity,
} from './decisionMap'

function alert(overrides: Partial<AlertRow> = {}): AlertRow {
  return {
    id: 1,
    pair: 'eurusd',
    timeframe: '15m',
    side: 'long',
    source: 'sr',
    zone_min: 99,
    zone_max: 100,
    entry: 100,
    stop: 98,
    target: 104,
    score: 70,
    confluence: 3,
    state: 'WATCHING',
    swept_price: null,
    size: 1,
    rr: 2,
    last_price: 101,
    unrealized_r: null,
    armed_at: 1000,
    updated_at: 1000,
    events: [],
    ...overrides,
  }
}

describe('decision map selection', () => {
  it('selects the current plan alert over a newer unrelated alert', () => {
    const matching = alert({ id: 1, updated_at: 1000 })
    const unrelated = alert({ id: 2, side: 'short', zone_min: 120, zone_max: 121, updated_at: 2000 })
    expect(selectFocusedAlert(
      [unrelated, matching],
      { side: 'long', zone_min: 99, zone_max: 100, entry_tf: '15m' },
      '15m',
    )?.id).toBe(1)
  })

  it('keeps only nearest liquidity above and below', () => {
    const levels = [
      { id: 'PDH', label: 'PDH', price: 105, period: 'day', side: 'high', source_epoch: 1 },
      { id: 'PWH', label: 'PWH', price: 110, period: 'week', side: 'high', source_epoch: 1 },
      { id: 'PDL', label: 'PDL', price: 95, period: 'day', side: 'low', source_epoch: 1 },
      { id: 'PWL', label: 'PWL', price: 90, period: 'week', side: 'low', source_epoch: 1 },
    ] as const
    expect(selectLiquidity([...levels], 100, 'nearby').map(level => level.id)).toEqual(['PDH', 'PDL'])
  })
})

describe('decision map events', () => {
  const candles: Candle[] = [
    { time: 1, open: 102, high: 103, low: 101, close: 102 },
    { time: 2, open: 101, high: 102, low: 99.5, close: 100.5 },
    { time: 3, open: 100, high: 101, low: 98.5, close: 99.5 },
  ]
  const story = {
    beats: [{ kind: 'sweep', level: 98.8 }],
  } as Story

  it('infers touch, sweep, and reclaim in order', () => {
    const events = inferDecisionEvents(candles, story, { side: 'long', zone_min: 99, zone_max: 100 })
    expect(events.map(event => event.kind)).toEqual(['touch', 'sweep', 'reclaim'])
    expect(events.every(event => event.source === 'candles')).toBe(true)
  })

  it('uses persisted alert events when they are available', () => {
    const row = alert({
      events: [
        { id: 1, alert_id: 1, state: 'SWEPT', ts: 3, price: 98.5, reason: 'swept' },
        { id: 2, alert_id: 1, state: 'FILLED', ts: 4, price: 99.5, reason: 'reclaimed' },
      ],
    })
    expect(alertDecisionEvents(row).map(event => event.kind)).toEqual(['sweep', 'reclaim'])
  })
})

it('supports legacy and explicit checklist states', () => {
  expect(checklistStatus({ stage: 'x', name: 'x', passed: true, detail: '' })).toBe('passed')
  expect(checklistStatus({ stage: 'x', name: 'x', passed: false, detail: '', status: 'pending' })).toBe('pending')
})
