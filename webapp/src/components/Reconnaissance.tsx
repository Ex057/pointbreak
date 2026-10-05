import type { ReconnaissanceResponse, ReconnaissanceTimeframe } from '../api/types'

function number(value: number | null, digits = 2) {
  if (value == null || !Number.isFinite(value)) return '—'
  return value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function signed(value: number | null, suffix = '') {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${number(value)}${suffix}`
}

function tone(score: number) {
  if (score >= 15) return 'bullish'
  if (score <= -15) return 'bearish'
  return 'neutral'
}

function stanceLabel(value: ReconnaissanceResponse['summary']['stance']) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter: string) => letter.toUpperCase())
}

function observedAt(epoch: number | null) {
  if (!epoch) return 'waiting for a completed candle'
  return new Date(epoch * 1000).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function Level({ label, level }: { label: string; level: ReconnaissanceTimeframe['levels']['support'] }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{level ? number(level.price, 5) : '—'}</dd>
      {level && <small>{level.type.replace(':', ' · ')} · {level.distance_pct}% away</small>}
    </div>
  )
}

function TimeframeCard({ item }: { item: ReconnaissanceTimeframe }) {
  const stateTone = tone(item.direction_score)
  return (
    <article className={`tg-recon-card tg-recon-card--${stateTone} ${!item.available ? 'tg-recon-card--off' : ''}`}>
      <div className="tg-recon-card-head">
        <strong>{item.timeframe}</strong>
        <span>{item.available ? signed(item.direction_score) : 'Unavailable'}</span>
      </div>
      {item.available ? (
        <>
          <div className="tg-recon-state">
            <strong>{item.structure}</strong>
            <span>{item.bias} bias · {Math.round(item.structure_confidence * 100)}% structure</span>
          </div>
          <dl className="tg-recon-levels">
            <Level label="Support / demand" level={item.levels.support} />
            <Level label="Resistance / supply" level={item.levels.resistance} />
          </dl>
          <div className="tg-recon-card-meta">
            <span>Volatility <strong>{item.volatility.regime}</strong>{item.volatility.atr_pct != null ? ` · ${item.volatility.atr_pct}% ATR` : ''}</span>
            <span>{item.candidate ? `${item.candidate.side ?? 'Setup'} candidate · ${item.candidate.source ?? 'projected'}` : 'No projected candidate'}</span>
            <span>{observedAt(item.last_complete_epoch)} · {item.bars} bars</span>
          </div>
        </>
      ) : <p>{item.error ?? item.reason}</p>}
    </article>
  )
}

export default function Reconnaissance({
  report,
  loading,
  error,
  onRefresh,
}: {
  report: ReconnaissanceResponse | null
  loading: boolean
  error: string | null
  onRefresh: () => void
}) {
  const summary = report?.summary
  const scoreTone = tone(summary?.score ?? 0)
  return (
    <section className="tg-recon">
      <div className="tg-recon-head">
        <div>
          <span className="tg-eyebrow">Multi-timeframe market map</span>
          <h2>Reconnaissance</h2>
          <p>{summary?.headline ?? 'Building the selected pair’s 30-day and multi-timeframe structure map.'}</p>
        </div>
        <div className="tg-recon-actions">
          {report && <span className={`tg-recon-status tg-recon-status--${report.status}`}>{report.status}</span>}
          <button className="tg-rescan" onClick={onRefresh} disabled={loading}>
            {loading ? 'Scanning…' : 'Refresh reconnaissance'}
          </button>
        </div>
      </div>

      {error && !report && <div className="tg-recon-error">{error}</div>}

      {summary && (
        <>
          <div className="tg-recon-overview">
            <div className={`tg-recon-call tg-recon-call--${scoreTone}`}>
              <span>Composite reading</span>
              <strong>{stanceLabel(summary.stance)}</strong>
              <small>{signed(summary.score)} score · {Math.round(summary.confidence * 100)}% confidence</small>
            </div>
            <div><span>30-day change</span><strong>{signed(summary.change_pct_30d, '%')}</strong></div>
            <div><span>30-day range</span><strong>{number(summary.low_30d, 5)} – {number(summary.high_30d, 5)}</strong></div>
            <div><span>Range position</span><strong>{summary.range_position == null ? '—' : `${Math.round(summary.range_position * 100)}%`}</strong></div>
            <div><span>30-day volatility</span><strong>{summary.volatility}</strong></div>
          </div>

          {report.conflicts.length > 0 && (
            <div className="tg-recon-conflicts">
              {report.conflicts.map(item => <span key={item}>Conflict · {item}</span>)}
            </div>
          )}

          <div className="tg-recon-grid">
            {report.timeframes.map(item => <TimeframeCard key={item.timeframe} item={item} />)}
          </div>

          <div className="tg-recon-foot">
            <span>Advisory only · uses completed candles · refreshes after each 15-minute close</span>
            <span>{report.cached ? 'cached' : 'fresh'} · generated {observedAt(report.generated_at)}</span>
          </div>
          {error && <div className="tg-recon-error">Latest refresh failed: {error}</div>}
        </>
      )}
    </section>
  )
}
