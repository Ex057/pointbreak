import { useEffect, useMemo, useState } from 'react'
import type { AiAnalysisResponse, AlertRow, ChecklistItem, ClosedTradeRow, EodReviewResponse, HistoryScanResult, JournalRow, MonitorStatus, Pagination, Recommendation, RescanMeta, StatsResponse } from '../api/types'
import { applyRecommendation, dismissRecommendation, fetchEodReview, fetchOverrides, fetchSettings, resetOverrides, runEodReview, scanHistory, updateSettings } from '../api/client'

const LIVE = new Set(['WATCHING', 'TOUCHED', 'SWEPT', 'FILLED'])
const STATE_STEPS = ['WATCHING', 'TOUCHED', 'SWEPT', 'FILLED'] as const

function stateTone(state: string): 'live' | 'win' | 'loss' | 'dead' {
  if (state === 'TARGET') return 'win'
  if (state === 'STOPPED') return 'loss'
  if (state === 'EXPIRED' || state === 'INVALIDATED') return 'dead'
  return 'live'
}

function timeAgo(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function fmtDateTime(ts: number) {
  const d = new Date(ts * 1000)
  const eat = new Date(d.getTime() + 3 * 60 * 60 * 1000)
  const yyyy = eat.getUTCFullYear()
  const mm = String(eat.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(eat.getUTCDate()).padStart(2, '0')
  const hh = String(eat.getUTCHours()).padStart(2, '0')
  const mi = String(eat.getUTCMinutes()).padStart(2, '0')
  const ss = String(eat.getUTCSeconds()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss} EAT`
}

function tradeSide(row: { side?: 'long' | 'short' | null; entry: number; target: number }): 'long' | 'short' {
  if (row.side === 'long' || row.side === 'short') return row.side
  return row.target >= row.entry ? 'long' : 'short'
}

function fmtPrice(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function replayLabel(row: ClosedTradeRow) {
  if (row.replay_mode === 'exact') return 'Exact'
  if (row.replay_mode === 'estimated') return 'Estimated'
  return 'Unavailable'
}

function fmtDistance(points?: number | null, pips?: number | null) {
  if (typeof points !== 'number' || !Number.isFinite(points)) return '—'
  if (typeof pips !== 'number' || !Number.isFinite(pips)) return `${points.toFixed(2)} pts`
  return `${points.toFixed(2)} pts / ${pips.toFixed(0)} pips`
}

function fmtPct(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round(value * 100)}%`
}

function waitingSummary(row: JournalRow) {
  const seen = Math.max(1, row.repeat_count ?? 1)
  const first = row.first_seen_at ?? row.created_at
  const last = row.last_seen_at ?? row.created_at
  const span = Math.max(0, last - first)
  if (seen <= 1 || span <= 0) return row.reason
  const mins = Math.floor(span / 60)
  const hours = Math.floor(mins / 60)
  const duration = hours > 0 ? `${hours}h ${mins % 60}m` : `${mins}m`
  return `${row.reason} · seen ${seen} times over ${duration}`
}

function freshnessState(currentPlan: {
  generated_at?: number
  rescan?: RescanMeta
} | null, journal: JournalRow[]) {
  const now = Math.floor(Date.now() / 1000)
  const latestJournal = journal[0]?.last_seen_at ?? journal[0]?.created_at ?? null
  const staleAfter = currentPlan?.rescan?.stale_after
  if (staleAfter && now > staleAfter) return { label: 'analysis stale', tone: 'tg-red' }
  if (latestJournal && now - latestJournal > 30 * 60) return { label: 'monitor quiet', tone: 'tg-red' }
  if (currentPlan?.rescan?.next_recheck_at && now < currentPlan.rescan.next_recheck_at) return { label: 'waiting for next bar', tone: 'tg-muted' }
  return { label: 're-analysis active', tone: 'tg-green' }
}

function emptyLedgerReason(journal: JournalRow[], currentPlan: { status: 'ENTER_READY' | 'WAITING' | 'DISCARDED'; entry_tf?: string } | null) {
  if (currentPlan?.status === 'WAITING') {
    return `0 trades executed because the current setup is still waiting for ${currentPlan.entry_tf ?? 'entry'} confirmation.`
  }
  if (journal.some(row => row.status === 'waiting')) {
    return '0 trades executed because conditions aligned but no trigger confirmed yet.'
  }
  if (journal.some(row => row.status === 'discarded' || row.status.startsWith('blocked_'))) {
    return '0 trades executed because current candidates were vetoed by structure, R:R, or monitor safety gates.'
  }
  return 'No closed trades in this date range.'
}

function exportClosedCsv(rows: ClosedTradeRow[]) {
  const head = [
    'closed_at',
    'pair',
    'timeframe',
    'side',
    'zone_type',
    'entry',
    'stop',
    'target',
    'result_r',
    'cash_delta',
    'balance',
  ]
  const body = rows.map(r =>
    [
      new Date(r.closed_at * 1000).toISOString(),
      r.pair,
      r.timeframe,
      r.side,
      r.zone_type,
      r.entry,
      r.stop,
      r.target,
      r.result_r,
      r.cash_delta,
      r.balance,
    ].join(','),
  )
  const blob = new Blob([[head.join(','), ...body].join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'tradeguard-paper-ledger.csv'
  a.click()
  URL.revokeObjectURL(url)
}

export default function PositionsPanel({
  monitor,
  alerts,
  pagination,
  stats,
  currentPlan,
  aiAnalysis,
  aiLoading,
  onRunAiAnalysis,
  selectedHistoryAlertId,
  onClose,
  onShowHistory,
  onPage,
  range,
  onRange,
  closingId,
}: {
  monitor: MonitorStatus | null
  alerts: AlertRow[]
  pagination: Pagination
  stats: StatsResponse | null
  currentPlan: {
    status: 'ENTER_READY' | 'WAITING' | 'DISCARDED'
    side: string | null
    entry: number | null
    stop: number | null
    target: number | null
    target_basis?: string | null
    notes?: string[]
    checklist?: ChecklistItem[]
    generated_at?: number
    rescan?: RescanMeta
    bias_tf?: string
    setup_tf?: string
    entry_tf?: string
    confirmation_state?: string
  } | null
  aiAnalysis: AiAnalysisResponse | null
  aiLoading: boolean
  onRunAiAnalysis: () => void
  selectedHistoryAlertId: number | null
  onClose: (id: number) => void
  onShowHistory: (row: ClosedTradeRow | null) => void
  onPage: (page: number) => void
  range: { from: string; to: string }
  onRange: (from: string, to: string) => void
  closingId: number | null
}) {
  const [dockTab, setDockTab] = useState<'live' | 'closed' | 'journal' | 'stats' | 'ai'>('closed')
  const account = stats?.account
  const s = stats?.stats
  const pnl = account ? account.balance - account.starting_balance : 0
  const floatingR = stats?.floating_r ?? 0
  const closed = stats?.closed ?? []
  const hasOpen = (stats?.open?.length ?? 0) > 0
  const paperPositions = alerts.filter(a => a.state === 'FILLED')
  const confirmedAlerts = alerts.filter(a => a.state !== 'FILLED')
  const canReplay = (row: ClosedTradeRow) =>
    row.entry != null && (row.exit_price ?? row.target) != null && row.exit_time != null && row.replay_mode !== 'unavailable'
  const journal = stats?.journal ?? []
  const sortedJournal = useMemo(
    () => [...journal].sort((a, b) => (b.last_seen_at ?? b.created_at) - (a.last_seen_at ?? a.created_at) || b.id - a.id),
    [journal],
  )
  const liveCount = paperPositions.length + confirmedAlerts.length
  const freshness = freshnessState(currentPlan, sortedJournal)

  useEffect(() => {
    if (liveCount > 0) {
      setDockTab(curr => (curr === 'closed' || curr === 'journal' || curr === 'stats' ? curr : 'live'))
      return
    }
    setDockTab(curr => (curr === 'live' ? 'closed' : curr))
  }, [liveCount])

  function preset(days: number) {
    const now = new Date()
    const to = now.toISOString().slice(0, 10)
    const from = new Date(now.getTime() - days * 86400000).toISOString().slice(0, 10)
    onRange(from, to)
  }

  return (
    <section className="tg-positions">
      <header className="tg-pos-head">
        <div className="tg-pos-title">
          <strong>Positions &amp; alerts</strong>
          <span className="tg-pos-sub">paper ledger · live monitor · export-ready</span>
        </div>
        {monitor && (
          <div className="tg-pos-monitor">
            <span className={`tg-dot ${monitor.running ? 'tg-dot--on' : ''}`} />
            monitoring <strong>{monitor.pair_display ?? monitor.pair ?? '—'}</strong>
            {monitor.timeframe && <span className="tg-muted"> · {monitor.timeframe}</span>}
            <span className="tg-muted"> · {monitor.active_alerts} live</span>
            <span className={freshness.tone}> · {freshness.label}</span>
          </div>
        )}
      </header>

      <div className="tg-workbench-tabs">
        <button className={`tg-workbench-tab ${dockTab === 'live' ? 'tg-workbench-tab--on' : ''}`} onClick={() => setDockTab('live')}>
          Live
        </button>
        <button className={`tg-workbench-tab ${dockTab === 'closed' ? 'tg-workbench-tab--on' : ''}`} onClick={() => setDockTab('closed')}>
          Closed Ledger
        </button>
        <button className={`tg-workbench-tab ${dockTab === 'journal' ? 'tg-workbench-tab--on' : ''}`} onClick={() => setDockTab('journal')}>
          Research Journal
        </button>
        <button className={`tg-workbench-tab ${dockTab === 'stats' ? 'tg-workbench-tab--on' : ''}`} onClick={() => setDockTab('stats')}>
          Stats
        </button>
        <button className={`tg-workbench-tab ${dockTab === 'ai' ? 'tg-workbench-tab--on' : ''}`} onClick={() => setDockTab('ai')}>
          AI Analyst
        </button>
      </div>

      {dockTab !== 'live' && dockTab !== 'ai' && (
        <div className="tg-ledger-tools">
          <div className="tg-ledger-range">
            <button className="tg-close-btn" onClick={() => preset(1)}>Today</button>
            <button className="tg-close-btn" onClick={() => preset(7)}>7D</button>
            <button className="tg-close-btn" onClick={() => preset(30)}>30D</button>
            <label>
              <span>From</span>
              <input type="date" value={range.from} onChange={e => onRange(e.target.value, range.to)} />
            </label>
            <label>
              <span>To</span>
              <input type="date" value={range.to} onChange={e => onRange(range.from, e.target.value)} />
            </label>
          </div>
          {dockTab === 'closed' && (
            <button className="tg-take-btn" onClick={() => exportClosedCsv(closed)} disabled={!closed.length}>
              Export CSV
            </button>
          )}
        </div>
      )}

      <div className="tg-workbench">
        {dockTab === 'live' && (
          <div className="tg-workbench-pane">
            <div className="tg-checklist-head">
              <strong>Live alerts and positions</strong>
              <span className="tg-pos-sub">
                page {pagination.page} of {pagination.total_pages}
              </span>
            </div>
            {alerts.length === 0 ? (
            <div className="tg-pos-empty">No confirmed live alerts in the selected pair.</div>
          ) : (
            <>
              {paperPositions.length > 0 && (
                <>
                  <div className="tg-checklist-head">
                    <strong>Paper positions</strong>
                    <span className="tg-pos-sub">{paperPositions.length} open</span>
                  </div>
                  <ul className="tg-alert-list">
                    {paperPositions.map(a => {
                      const last = a.events[a.events.length - 1]
                      return (
                        <li key={a.id} className="tg-alert tg-alert--live">
                          <div className="tg-alert-top">
                            <span className={`tg-side tg-side--${a.side}`}>{a.side.toUpperCase()}</span>
                            <span className="tg-alert-src">{a.source}</span>
                            <span className="tg-alert-state tg-alert-state--live">FILLED</span>
                            <span className="tg-alert-rr">{a.rr.toFixed(1)}R</span>
                            <button className="tg-close-btn" onClick={() => onClose(a.id)} disabled={closingId === a.id}>
                              {closingId === a.id ? 'Closing…' : 'Close'}
                            </button>
                          </div>
                          <div className="tg-alert-levels">
                            <span>{a.pair}</span>
                            <span>entry {a.entry.toFixed(2)}</span>
                            <span className="tg-red">stop {a.stop.toFixed(2)}</span>
                            <span className="tg-green">target {a.target.toFixed(2)}</span>
                            {a.unrealized_r != null && (
                              <span className={a.unrealized_r >= 0 ? 'tg-green' : 'tg-red'}>
                                running {a.unrealized_r >= 0 ? '+' : ''}{a.unrealized_r.toFixed(2)}R
                              </span>
                            )}
                          </div>
                          <div className="tg-alert-note">
                            {a.entry_tf ?? '15m'} confirmed · {a.resolved_symbol ?? a.pair} · {last?.reason ?? 'live paper trade'}
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                </>
              )}
              {confirmedAlerts.length > 0 && (
                <>
                  <div className="tg-checklist-head">
                    <strong>Confirmed alerts</strong>
                    <span className="tg-pos-sub">{confirmedAlerts.length} waiting to play out</span>
                  </div>
                  <ul className="tg-alert-list">
                    {confirmedAlerts.map(a => {
                  const tone = stateTone(a.state)
                  const live = LIVE.has(a.state)
                  const last = a.events[a.events.length - 1]
                  return (
                    <li key={a.id} className={`tg-alert tg-alert--${tone}`}>
                      <div className="tg-alert-top">
                        <span className={`tg-side tg-side--${a.side}`}>{a.side.toUpperCase()}</span>
                        <span className="tg-alert-src">{a.source}</span>
                        <span className={`tg-alert-state tg-alert-state--${tone}`}>{a.state}</span>
                        <span className="tg-alert-rr">{a.rr.toFixed(1)}R</span>
                        {live && (
                          <button className="tg-close-btn" onClick={() => onClose(a.id)} disabled={closingId === a.id}>
                            {closingId === a.id ? 'Closing…' : 'Close'}
                          </button>
                        )}
                      </div>

                      {live && a.state !== 'FILLED' && (
                        <div className="tg-steps">
                          {STATE_STEPS.map(step => {
                            const idx = STATE_STEPS.indexOf(a.state as (typeof STATE_STEPS)[number])
                            const here = STATE_STEPS.indexOf(step)
                            const done = idx >= 0 && here <= idx
                            return <span key={step} className={`tg-step ${done ? 'tg-step--done' : ''}`}>{step.toLowerCase()}</span>
                          })}
                        </div>
                      )}

                      <div className="tg-alert-levels">
                        <span>{a.pair}</span>
                        <span>entry {a.entry.toFixed(2)}</span>
                        <span className="tg-red">stop {a.stop.toFixed(2)}</span>
                        <span className="tg-green">target {a.target.toFixed(2)}</span>
                        {a.state === 'FILLED' && a.unrealized_r != null && (
                          <span className={a.unrealized_r >= 0 ? 'tg-green' : 'tg-red'}>
                            running {a.unrealized_r >= 0 ? '+' : ''}{a.unrealized_r.toFixed(2)}R
                          </span>
                        )}
                      </div>

                      {last && <div className="tg-alert-note">{last.reason} · <span className="tg-muted">{timeAgo(last.ts)}</span></div>}
                    </li>
                  )
                    })}
                  </ul>
                </>
              )}
              <div className="tg-alert-pager">
                <span className="tg-pos-sub">{pagination.total} total alerts</span>
                <div className="tg-alert-pager-actions">
                  <button className="tg-close-btn" disabled={pagination.page <= 1} onClick={() => onPage(pagination.page - 1)}>Previous</button>
                  <button className="tg-close-btn" disabled={pagination.page >= pagination.total_pages} onClick={() => onPage(pagination.page + 1)}>Next</button>
                </div>
              </div>
            </>
          )}
          </div>
        )}

        {dockTab === 'closed' && (
        <div className="tg-workbench-pane">
          <div className="tg-checklist-head">
            <strong>Closed ledger</strong>
            <span className="tg-pos-sub">{closed.length} closed trades in range</span>
          </div>
          {closed.length === 0 ? (
            <div className="tg-pos-empty">{emptyLedgerReason(sortedJournal, currentPlan)}</div>
          ) : (
            <div className="tg-ledger-table-wrap">
              <table className="tg-ledger-table">
                <thead>
                  <tr>
                    <th>Date / time</th>
                    <th>Pair</th>
                    <th>Side</th>
                    <th>TF</th>
                    <th>Entry</th>
                    <th>Close</th>
                    <th>Dist</th>
                    <th>Replay</th>
                    <th>Result</th>
                    <th>Cash</th>
                    <th>Balance</th>
                    <th>Chart</th>
                  </tr>
                </thead>
                <tbody>
                  {closed.map(row => (
                    <tr key={`${row.alert_id}-${row.closed_at}`}>
                      <td>{fmtDateTime(row.closed_at)}</td>
                      <td>{row.pair}</td>
                      <td>{row.entry != null && row.target != null ? tradeSide(row).toUpperCase() : '—'}</td>
                      <td>{row.timeframe}</td>
                      <td>{fmtPrice(row.entry)}</td>
                      <td>{fmtPrice(row.exit_price ?? row.target)}</td>
                      <td title={`SL ${fmtDistance(row.stop_distance_points, row.stop_distance_pips)} · TP ${fmtDistance(row.target_distance_points, row.target_distance_pips)}`}>
                        {fmtDistance(row.entry_to_close_points, row.entry_to_close_pips)}
                      </td>
                      <td title={row.estimated_replay_reason ?? ''}>{replayLabel(row)}</td>
                      <td className={row.result_r >= 0 ? 'tg-green' : 'tg-red'}>
                        {row.result_r >= 0 ? '+' : ''}{row.result_r}R
                      </td>
                      <td className={row.cash_delta >= 0 ? 'tg-green' : 'tg-red'}>
                        {row.cash_delta >= 0 ? '+' : ''}{row.cash_delta.toFixed(2)}
                      </td>
                      <td>{row.balance.toFixed(2)}</td>
                      <td>
                        <button
                          className="tg-close-btn"
                          disabled={!canReplay(row)}
                          title={
                            canReplay(row)
                              ? row.replay_mode === 'estimated'
                                ? row.estimated_replay_reason ?? 'Estimated replay'
                                : 'Show this trade on the chart'
                              : 'Trade history is incomplete and cannot be replayed'
                          }
                          onClick={() =>
                            onShowHistory(selectedHistoryAlertId === row.alert_id ? null : row)
                          }
                        >
                          {selectedHistoryAlertId === row.alert_id ? 'Hide' : 'Show on chart'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        )}
        {dockTab === 'journal' && (
        <div className="tg-workbench-pane">
            <div className="tg-checklist-head">
              <strong>Research journal</strong>
            <span className="tg-pos-sub">{sortedJournal.length} setup events · sorted by last re-analysis</span>
          </div>
          {sortedJournal.length === 0 ? (
            <div className="tg-pos-empty">No setup journal rows in this range.</div>
          ) : (
            <div className="tg-ledger-table-wrap">
              <table className="tg-ledger-table">
                <thead>
                  <tr>
                    <th>Last seen</th>
                    <th>First seen</th>
                    <th>Status</th>
                    <th>Side</th>
                    <th>TF</th>
                    <th>Entry</th>
                    <th>Feed</th>
                    <th>Seen</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedJournal.map((row: JournalRow) => (
                    <tr key={row.id}>
                      <td title="Most recent re-analysis time">{fmtDateTime(row.last_seen_at ?? row.created_at)}</td>
                      <td title="When this setup fingerprint was first observed">{fmtDateTime(row.first_seen_at ?? row.created_at)}</td>
                      <td>{row.status}</td>
                      <td>{row.side?.toUpperCase() ?? '—'}</td>
                      <td>{row.timeframe}</td>
                      <td>{fmtPrice(row.entry)}</td>
                      <td>{row.feed_source ?? '—'}</td>
                      <td>{row.repeat_count ?? 1}</td>
                      <td title={row.reason}>{row.status === 'waiting' ? waitingSummary(row) : row.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        )}
        {dockTab === 'stats' && (
          <div className="tg-workbench-pane">
            <div className="tg-checklist-head">
              <strong>Stats</strong>
              <span className="tg-pos-sub">range metrics and pair summaries</span>
            </div>
            {account ? (
              <div className="tg-account">
                <Metric label="Paper balance" value={account.balance.toFixed(2)} />
                <Metric label="Lifetime P&L" value={`${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}`} tone={pnl >= 0 ? 'green' : 'red'} />
                <Metric label="Range trades" value={String(s?.trades ?? 0)} />
                <Metric label="Range win rate" value={s && s.trades ? `${s.win_rate}%` : '—'} />
                <Metric label="Range net R" value={s ? `${s.net_r >= 0 ? '+' : ''}${s.net_r}` : '—'} tone={s && s.net_r >= 0 ? 'green' : 'red'} />
                <Metric label="Range cash" value={s ? `${s.net_cash >= 0 ? '+' : ''}${s.net_cash.toFixed(2)}` : '—'} tone={s && s.net_cash >= 0 ? 'green' : 'red'} />
                <Metric label="W / L" value={`${account.wins} / ${account.losses}`} />
                {hasOpen && (
                  <>
                    <Metric label="Floating R" value={`${floatingR >= 0 ? '+' : ''}${floatingR}`} tone={floatingR >= 0 ? 'green' : 'red'} />
                    <Metric label="Equity (incl. open)" value={(stats?.equity_float ?? account.balance).toFixed(2)} />
                  </>
                )}
              </div>
            ) : (
              <div className="tg-pos-empty">No account stats yet.</div>
            )}
            {s?.by_pair?.length ? (
              <div className="tg-ledger-pairs">
                {s.by_pair.map(row => (
                  <div key={row.pair} className="tg-ledger-pair">
                    <strong>{row.pair}</strong>
                    <span>{row.trades} trades</span>
                    <span className={row.net_cash >= 0 ? 'tg-green' : 'tg-red'}>
                      {row.net_cash >= 0 ? '+' : ''}{row.net_cash.toFixed(2)}
                    </span>
                    <span>{row.net_r >= 0 ? '+' : ''}{row.net_r}R</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="tg-pos-empty">No pair summaries yet.</div>
            )}
          </div>
        )}
        {dockTab === 'ai' && (
          <div className="tg-workbench-pane">
            <div className="tg-checklist-head">
              <strong>AI Analyst</strong>
              <div className="tg-alert-pager-actions">
                <span className="tg-pos-sub">
                  {aiAnalysis ? `last run ${fmtDateTime(aiAnalysis.generated_at)}` : 'current pair only'}
                </span>
                <button className="tg-take-btn" onClick={onRunAiAnalysis} disabled={aiLoading}>
                  {aiLoading ? 'Running…' : 'Run analysis'}
                </button>
              </div>
            </div>
            {!aiAnalysis ? (
              <div className="tg-pos-empty">
                {aiLoading ? 'Running AI Analyst…' : 'No AI analysis yet for this pair.'}
              </div>
            ) : (
              <div className="tg-analyst">
                <div className="tg-analyst-top">
                  <div>
                    <span className={`tg-analyst-badge tg-analyst-badge--${aiAnalysis.analysis.agreement.state}`}>
                      AI {aiAnalysis.analysis.agreement.state}
                    </span>
                    <strong className="tg-analyst-label">{aiAnalysis.analysis.trade_framing.label}</strong>
                    <span className="tg-pos-sub">
                      {aiAnalysis.analysis.agreement.confidence} confidence · {aiAnalysis.analysis.provider_label ?? aiAnalysis.analysis.provider}
                    </span>
                  </div>
                </div>

                <div className="tg-analyst-levels">
                  {aiAnalysis.analysis.trade_framing.entry_zone && (
                    <p>
                      Entry zone: <strong>{aiAnalysis.analysis.trade_framing.entry_zone.min.toFixed(2)} – {aiAnalysis.analysis.trade_framing.entry_zone.max.toFixed(2)}</strong>
                    </p>
                  )}
                  {aiAnalysis.analysis.trade_framing.stop_loss != null && (
                    <p>Stop loss: <strong>{aiAnalysis.analysis.trade_framing.stop_loss.toFixed(2)}</strong></p>
                  )}
                  {aiAnalysis.analysis.trade_framing.take_profit_1 != null && (
                    <p>Take profit 1: <strong>{aiAnalysis.analysis.trade_framing.take_profit_1.toFixed(2)}</strong></p>
                  )}
                  {aiAnalysis.analysis.trade_framing.take_profit_2 != null && (
                    <p>Take profit 2: <strong>{aiAnalysis.analysis.trade_framing.take_profit_2.toFixed(2)}</strong></p>
                  )}
                </div>

                <div className="tg-analyst-block">
                  <strong>AI reasoning</strong>
                  <p>{aiAnalysis.analysis.reasoning}</p>
                </div>

                {aiAnalysis.analysis.why_now.length > 0 && (
                  <div className="tg-analyst-block">
                    <strong>Why now</strong>
                    <ul className="tg-macro-list">
                      {aiAnalysis.analysis.why_now.map((line, i) => (
                        <li key={i}><span>{line}</span></li>
                      ))}
                    </ul>
                  </div>
                )}

                {aiAnalysis.analysis.what_is_missing.length > 0 && (
                  <div className="tg-analyst-block">
                    <strong>What is missing</strong>
                    <ul className="tg-macro-list">
                      {aiAnalysis.analysis.what_is_missing.map((line, i) => (
                        <li key={i}><span>{line}</span></li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="tg-analyst-block">
                  <strong>History check</strong>
                  <p>{aiAnalysis.analysis.history_check.summary}</p>
                  <p className="tg-pos-sub">
                    sample {aiAnalysis.analysis.history_check.sample_size} · targets {aiAnalysis.analysis.history_check.counts.targets} · stopped {aiAnalysis.analysis.history_check.counts.stopped} · retired {aiAnalysis.analysis.history_check.counts.retired}
                  </p>
                </div>

                {aiAnalysis.analysis.learned_score && (
                  <div className="tg-analyst-block">
                    <strong>Current learned score</strong>
                    {aiAnalysis.analysis.learned_score.quality_score != null ? (
                      <>
                        <div className="tg-account">
                          <Metric label="Quality score" value={`${aiAnalysis.analysis.learned_score.quality_score}/100`} />
                          <Metric label="Fill probability" value={fmtPct(aiAnalysis.analysis.learned_score.fill_probability)} />
                          <Metric label="Target probability" value={fmtPct(aiAnalysis.analysis.learned_score.target_probability)} />
                          <Metric label="Retire risk" value={fmtPct(aiAnalysis.analysis.learned_score.retire_risk)} tone={(aiAnalysis.analysis.learned_score.retire_risk ?? 0) > 0.4 ? 'red' : 'green'} />
                        </div>
                        <p className="tg-pos-sub">
                          model {aiAnalysis.analysis.learned_score.model_version ?? '—'} · {aiAnalysis.analysis.learned_score.model_confidence} confidence · training sample {aiAnalysis.analysis.learned_score.training_sample_count ?? 0}
                        </p>
                      </>
                    ) : (
                      <p>{aiAnalysis.analysis.learned_score.status === 'insufficient_history' ? 'Learning in progress — not enough clean history yet for a scored setup.' : 'No learned score available for this setup yet.'}</p>
                    )}
                  </div>
                )}

                {aiAnalysis.analysis.learning_status && (
                  <div className="tg-analyst-block">
                    <strong>Learning progress</strong>
                    <p>
                      {aiAnalysis.analysis.learning_status.enabled
                        ? `Active model ${aiAnalysis.analysis.learning_status.active_model_version ?? 'not promoted yet'} · ${aiAnalysis.analysis.learning_status.sample_count} samples`
                        : aiAnalysis.analysis.learning_status.promotion_reason ?? 'Learning is disabled for this pair.'}
                    </p>
                    {aiAnalysis.analysis.learning_status.enabled && (
                      <>
                        <p className="tg-pos-sub">
                          last retrained {aiAnalysis.analysis.learning_status.last_retrained ? fmtDateTime(aiAnalysis.analysis.learning_status.last_retrained) : '—'} · holdout {aiAnalysis.analysis.learning_status.holdout_size} · result {aiAnalysis.analysis.learning_status.promotion_result ?? '—'}
                        </p>
                        <p className="tg-pos-sub">
                          {aiAnalysis.analysis.learning_status.promotion_reason}
                        </p>
                        <p className="tg-pos-sub">
                          holdout candidate MAE {aiAnalysis.analysis.learning_status.metrics.candidate_mae ?? '—'} · baseline MAE {aiAnalysis.analysis.learning_status.metrics.baseline_mae ?? '—'}
                        </p>
                      </>
                    )}
                  </div>
                )}

                {aiAnalysis.analysis.detection_gaps && aiAnalysis.analysis.detection_gaps.blind_count > 0 && (
                  <div className="tg-analyst-block">
                    <strong>Detection blind spots ({aiAnalysis.analysis.detection_gaps.day})</strong>
                    <p>
                      {aiAnalysis.analysis.detection_gaps.blind_count} recent move(s) made ≥ target R with no candidate
                      proposed — biggest {aiAnalysis.analysis.detection_gaps.biggest_blind_r.toFixed(1)}R. The planner is
                      missing these, not vetoing them.
                    </p>
                    {aiAnalysis.analysis.detection_gaps.top.map((m, i) => (
                      <p key={i} className="tg-pos-sub">
                        {m.direction === 'long' ? 'BUY' : 'SELL'} {fmtPrice(m.origin_price)} → {fmtPrice(m.peak_price)} ({m.rr.toFixed(1)}R)
                        {m.formation?.summary ? ` — ${m.formation.summary}` : ''}
                      </p>
                    ))}
                  </div>
                )}

                <div className="tg-analyst-block">
                  <strong>Risk note</strong>
                  <p>{aiAnalysis.analysis.risk_note}</p>
                  {aiAnalysis.analysis.fallback_used && (
                    <p className="tg-pos-sub">Fallback used because the external provider was unavailable.</p>
                  )}
                  {aiAnalysis.analysis.error && (
                    <p className="tg-red">{aiAnalysis.analysis.error}</p>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        <EodReviewPanel pair={monitor?.pair ?? null} />
      </div>
    </section>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'green' | 'red' }) {
  return (
    <div className="tg-metric">
      <span className="tg-metric-label">{label}</span>
      <strong className={tone ? `tg-${tone}` : undefined}>{value}</strong>
    </div>
  )
}

function rSign(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}R`
}

function EodReviewPanel({ pair }: { pair: string | null }) {
  const [data, setData] = useState<EodReviewResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scan, setScan] = useState<HistoryScanResult | null>(null)
  const [scanning, setScanning] = useState(false)
  const [scanPage, setScanPage] = useState(0)
  const [autoApply, setAutoApply] = useState(false)
  const [overrides, setOverrides] = useState<Record<string, number>>({})
  const SCAN_PAGE_SIZE = 8

  const load = useMemo(
    () => async () => {
      if (!pair) return
      try {
        setData(await fetchEodReview(pair))
      } catch {
        /* review may not exist yet — leave data null */
      }
    },
    [pair],
  )

  const loadOverrides = useMemo(
    () => async () => {
      if (!pair) return
      try {
        setOverrides((await fetchOverrides(pair)).overrides)
      } catch {
        /* ignore */
      }
    },
    [pair],
  )

  useEffect(() => {
    setData(null)
    setScan(null)
    setScanPage(0)
    load()
    loadOverrides()
    fetchSettings().then((s) => setAutoApply(Boolean(s.settings.auto_apply))).catch(() => {})
  }, [load, loadOverrides])

  if (!pair) return null

  async function runScan() {
    if (!pair) return
    setScanning(true)
    setError(null)
    try {
      setScan(await scanHistory(pair))
      setScanPage(0)
      await loadOverrides()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'History scan failed.')
    } finally {
      setScanning(false)
    }
  }

  async function toggleAutoApply(next: boolean) {
    if (!pair) return
    setAutoApply(next)
    try {
      await updateSettings({ auto_apply: next })
    } catch {
      setAutoApply(!next) // revert on failure
    }
  }

  async function reset() {
    if (!pair) return
    try {
      await resetOverrides(pair)
      await Promise.all([loadOverrides(), load()])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reset failed.')
    }
  }

  const overrideKeys = Object.keys(overrides)

  const review = data?.latest?.review ?? null
  const recs = data?.recommendations ?? []
  // reviews stored before newer fields shipped may lack these arrays — never .filter(undefined)
  const gates = review?.gates ?? []
  const missedMoves = review?.missed_moves ?? []
  const flaws = review?.flaws ?? []

  async function run() {
    if (!pair) return
    setLoading(true)
    setError(null)
    try {
      const result = await runEodReview(pair)
      setData((prev) => ({
        pair,
        latest: {
          id: result.review_id,
          pair,
          day: result.review.day,
          realized_r: result.review.realized_r,
          missed_r: result.review.missed_r,
          provider: result.provider,
          created_at: Math.floor(Date.now() / 1000),
          review: result.review,
          flags: result.review.flaws,
        },
        history: prev?.history ?? [],
        recommendations: result.recommendations,
      }))
      await loadOverrides()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'End-of-day review failed.')
    } finally {
      setLoading(false)
    }
  }

  async function decide(recId: number, apply: boolean) {
    setBusyId(recId)
    try {
      if (apply) await applyRecommendation(recId)
      else await dismissRecommendation(recId)
      await Promise.all([load(), loadOverrides()])
    } finally {
      setBusyId(null)
    }
  }

  function renderRecs(list: Recommendation[]) {
    if (!list.length) return null
    return (
      <ul className="tg-eod-recs">
        {list.map((rec) => (
          <li key={rec.id} className="tg-eod-rec">
            <div className="tg-eod-rec-head">
              <span>
                <strong>{rec.param}</strong>: {rec.current_value ?? '—'} → <strong>{rec.proposed_value ?? '—'}</strong>
                {rec.confidence ? ` · ${rec.confidence} confidence` : ''}
              </span>
              <span className="tg-eod-rec-actions">
                {rec.status === 'auto_applied' || rec.status === 'applied' ? (
                  <span className="tg-analyst-badge tg-analyst-badge--agrees">{rec.status === 'auto_applied' ? 'auto-applied' : 'applied'}</span>
                ) : rec.status === 'dismissed' ? (
                  <span className="tg-pos-sub">dismissed</span>
                ) : (
                  <>
                    <button className="tg-take-btn" disabled={busyId === rec.id} onClick={() => decide(rec.id, true)}>Approve</button>
                    <button className="tg-close-btn" disabled={busyId === rec.id} onClick={() => decide(rec.id, false)}>Dismiss</button>
                  </>
                )}
              </span>
            </div>
            {rec.rationale && <p className="tg-pos-sub">{rec.rationale}</p>}
            <p className="tg-pos-sub">
              shadow impact {rec.shadow_delta_r != null ? rSign(rec.shadow_delta_r) : 'n/a'}
              {rec.shadow_added_trades != null ? ` across ${rec.shadow_added_trades} added fill(s)` : ''}
            </p>
          </li>
        ))}
      </ul>
    )
  }

  return (
    <div className="tg-analyst-block tg-eod-card">
      <div className="tg-analyst-head">
        <h3>End-of-day what-if review</h3>
        <span className="tg-eod-rec-actions">
          <button className="tg-take-btn" onClick={run} disabled={loading || scanning}>
            {loading ? 'Replaying…' : review ? 'Re-run for today' : 'Run end-of-day review'}
          </button>
          <button className="tg-take-btn" onClick={runScan} disabled={scanning || loading}>
            {scanning ? 'Scanning history…' : 'Scan recorded history'}
          </button>
        </span>
      </div>
      <p className="tg-pos-sub">
        "Re-run for today" replays today only. "Scan recorded history" replays every recorded day to
        surface <strong>all</strong> missed opportunities — with no candidate, or seen-then-vetoed — and how each formed.
      </p>

      <div className="tg-eod-controls">
        <label className="tg-eod-toggle">
          <input type="checkbox" checked={autoApply} onChange={(e) => toggleAutoApply(e.target.checked)} />
          <span>Auto-apply tuning</span>
        </label>
        <span className="tg-pos-sub">
          {autoApply
            ? 'On — every review/scan applies bounds-checked gate tweaks automatically.'
            : 'Off — recommendations wait for your Approve/Dismiss.'}
        </span>
      </div>

      {overrideKeys.length > 0 && (
        <div className="tg-eod-controls">
          <span className="tg-pos-sub">
            Active tuning: {overrideKeys.map((k) => `${k} ${k === 'counter_trend' || k === 'sweep_reclaim' ? (overrides[k] >= 1 ? 'on' : 'off') : overrides[k]}`).join(' · ')}
          </span>
          <button className="tg-close-btn" onClick={reset}>Reset to defaults</button>
        </div>
      )}
      {error && <p className="tg-red">{error}</p>}

      {scan && (
        <div className="tg-analyst-block">
          <strong>History scan — {scan.summary}</strong>
          {scan.opportunities.length === 0 ? (
            <p className="tg-pos-sub">
              Nothing to show yet. This scans stored candles ({scan.scan_tf ?? 'no timeframe'}) — let the feed run so
              intraday history accumulates, then scan again.
            </p>
          ) : (
            <>
              <ul className="tg-eod-recs">
                {scan.opportunities
                  .slice(scanPage * SCAN_PAGE_SIZE, scanPage * SCAN_PAGE_SIZE + SCAN_PAGE_SIZE)
                  .map((o, i) => (
                    <li key={scanPage * SCAN_PAGE_SIZE + i} className="tg-eod-rec">
                      <div className="tg-eod-rec-head">
                        <span>
                          <strong>{o.direction === 'long' ? 'BUY' : 'SELL'}</strong> entry {fmtPrice(o.entry ?? o.origin_price)} → {fmtPrice(o.peak_price)}
                          {o.stop != null ? `, stop ${fmtPrice(o.stop)}` : ''} · <strong>{o.rr.toFixed(1)}R</strong>
                          {' '}· <span className={o.coverage_kind === 'none' ? 'tg-red' : undefined}>{o.coverage_kind === 'none' ? 'no candidate' : 'seen → vetoed'}</span>
                        </span>
                        <span className="tg-pos-sub">{o.day} · {fmtDateTime(o.origin_epoch)}</span>
                      </div>
                      {o.formation?.summary && <p className="tg-pos-sub">Why missed / how it formed: {o.formation.summary}</p>}
                    </li>
                  ))}
              </ul>
              {scan.opportunities.length > SCAN_PAGE_SIZE && (
                <div className="tg-pager">
                  <button
                    className="tg-close-btn"
                    disabled={scanPage <= 0}
                    onClick={() => setScanPage((p) => Math.max(0, p - 1))}
                  >
                    Previous
                  </button>
                  <span className="tg-pos-sub">
                    {scanPage * SCAN_PAGE_SIZE + 1}–{Math.min(scan.opportunities.length, (scanPage + 1) * SCAN_PAGE_SIZE)} of {scan.opportunities.length}
                  </span>
                  <button
                    className="tg-close-btn"
                    disabled={(scanPage + 1) * SCAN_PAGE_SIZE >= scan.opportunities.length}
                    onClick={() => setScanPage((p) => p + 1)}
                  >
                    Next
                  </button>
                </div>
              )}
            </>
          )}
          {scan.recommendations && scan.recommendations.length > 0 && (
            <div className="tg-analyst-block">
              <strong>Recommended changes from history {scan.auto_apply ? '(auto-applied)' : '(shadow-tested)'}</strong>
              {renderRecs(scan.recommendations)}
            </div>
          )}
        </div>
      )}

      {!review ? (
        <p className="tg-pos-sub">No end-of-day review for {pair.toUpperCase()} yet. Run one after the session.</p>
      ) : (
        <>
          <p>{review.summary}</p>
          <div className="tg-account">
            <Metric label="Booked" value={rSign(review.realized_r)} tone={(review.realized_r ?? 0) >= 0 ? 'green' : 'red'} />
            <Metric label="Missed (rejected winners)" value={rSign(review.missed_r)} tone={(review.missed_r ?? 0) > 0 ? 'red' : 'green'} />
            <Metric label="Avoided losses" value={`${(review.avoided_loss_r ?? 0).toFixed(2)}R`} tone="green" />
            <Metric label="Rejections replayed" value={`${review.rejections_scored ?? 0}/${review.rejections_total ?? 0}`} />
          </div>

          {gates.filter((g) => g.missed_r > 0 || g.flaw).length > 0 && (
            <div className="tg-analyst-block">
              <strong>Where profit leaked, by veto gate</strong>
              <table className="tg-eod-table">
                <thead>
                  <tr><th>Gate</th><th>Rejected</th><th>Would win</th><th>Missed R</th></tr>
                </thead>
                <tbody>
                  {gates
                    .filter((g) => g.missed_r > 0 || g.flaw)
                    .slice(0, 6)
                    .map((g) => (
                      <tr key={g.gate} className={g.flaw ? 'tg-eod-flaw' : undefined}>
                        <td>{g.label}{g.flaw ? ' ⚑' : ''}</td>
                        <td>{g.count}</td>
                        <td>{g.would_have_won}{g.win_rate != null ? ` (${Math.round(g.win_rate * 100)}%)` : ''}</td>
                        <td className="tg-red">+{g.missed_r.toFixed(2)}R</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          {missedMoves.filter((m) => m.coverage_kind === 'none').length > 0 && (
            <div className="tg-analyst-block">
              <strong>Detection blind spots — moves with no candidate ({review.missed_moves_blind ?? missedMoves.filter((m) => m.coverage_kind === 'none').length})</strong>
              <p className="tg-pos-sub">
                Real swings that made ≥ target R which the planner never proposed an entry for — nothing was even
                journaled to veto. Each shows <em>how it formed</em>, so the setup can be learned and caught next time.
              </p>
              <ul className="tg-eod-recs">
                {missedMoves
                  .filter((m) => m.coverage_kind === 'none')
                  .slice(0, 6)
                  .map((m, i) => (
                    <li key={i} className="tg-eod-rec">
                      <div className="tg-eod-rec-head">
                        <span>
                          <strong>{m.direction === 'long' ? 'BUY' : 'SELL'}</strong> {fmtPrice(m.origin_price)} → {fmtPrice(m.peak_price)}
                          {' '}· <strong>{m.rr.toFixed(1)}R</strong>
                        </span>
                        <span className="tg-pos-sub">{fmtDateTime(m.origin_epoch)}</span>
                      </div>
                      {m.formation?.summary && (
                        <p className="tg-pos-sub">Formation: {m.formation.summary}</p>
                      )}
                    </li>
                  ))}
              </ul>
            </div>
          )}

          {flaws.length > 0 && (
            <div className="tg-analyst-block">
              <strong>Flagged flaws</strong>
              <ul className="tg-analyst-list">
                {flaws.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}
        </>
      )}

      {recs.length > 0 && (
        <div className="tg-analyst-block">
          <strong>Recommended changes ({data?.latest?.provider === 'claude' ? 'Claude' : 'built-in'} analyst · shadow-tested)</strong>
          {renderRecs(recs)}
        </div>
      )}
    </div>
  )
}
