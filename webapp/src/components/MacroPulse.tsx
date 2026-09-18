import type { HealthResponse, PriceTick, ScanResult } from '../api/types'

function toneClass(value: number) {
  if (value > 0) return 'tg-green'
  if (value < 0) return 'tg-red'
  return 'tg-muted'
}

function summaryBias(data: ScanResult) {
  const structure = data.structure?.state ?? 'UNDETERMINED'
  const verdict = data.verdict.state
  const plan = data.verdict.plan
  const score = plan?.score ?? 0
  if (verdict === 'ENTER') return 'Aligned'
  if (verdict === 'STAND_ASIDE') return 'Risk off'
  if (structure === 'UPTREND' || structure === 'DOWNTREND') return `${structure.toLowerCase()} watch`
  if (score >= 0.75) return 'High watchlist'
  return 'Neutral'
}

export default function MacroPulse({
  data,
  health,
  livePrice,
}: {
  data: ScanResult
  health: HealthResponse | null
  livePrice: PriceTick | null
}) {
  const plan = data.verdict.plan
  const event = data.verdict.news
  const macro = data.verdict.macro
  const sentiment = macro?.sentiment
  const projected = data.future.entries.length
  const liveAge = livePrice?.age
  const riskDelta =
    plan?.entry != null && livePrice?.price != null ? Number((livePrice.price - plan.entry).toFixed(2)) : null

  return (
    <aside className="tg-checklist">
      <div className="tg-checklist-head">
        <strong>Macro Pulse</strong>
      </div>

      <div className="tg-macro-grid">
        <PulseCard
          label="Verdict pulse"
          value={summaryBias(data)}
          detail={`${data.verdict.state} · ${data.structure.state.toLowerCase()}`}
        />
        <PulseCard
          label="Live feed"
          value={health?.feed_status.connected ? 'Connected' : 'Degraded'}
          detail={
            liveAge != null
              ? `${livePrice?.source ?? 'none'} · ${liveAge.toFixed(1)}s old`
              : health?.feed_status.error ?? 'waiting for tick'
          }
        />
        <PulseCard
          label="Projected ideas"
          value={String(projected)}
          detail={
            data.verdict.plan?.setup_tf && data.verdict.plan?.entry_tf
              ? `${data.verdict.plan.setup_tf} setup · ${data.verdict.plan.entry_tf} trigger`
              : projected
                ? `${data.future.entries[0].side} closest`
                : 'no active setup'
          }
        />
        <PulseCard
          label="News risk"
          value={event ? `${event.impact} impact` : 'Clear'}
          detail={
            event
              ? `${event.currency} in ${event.minutes_away} min`
              : `calendar ${macro?.provider_status?.calendar ?? 'disabled'} · headlines ${macro?.provider_status?.headlines ?? macro?.provider_status?.news ?? 'disabled'}`
          }
        />
      </div>

      <div className="tg-macro-block">
        <strong>Sentiment scorecard</strong>
        {sentiment ? (
          <>
            <p>
              Bias:{' '}
              <span className={toneClass(sentiment.score)}>
                {sentiment.label} ({sentiment.score >= 0 ? '+' : ''}{sentiment.score.toFixed(2)})
              </span>
            </p>
            <p>
              Confidence: <span>{sentiment.confidence}</span>
            </p>
            <p>
              Drivers: <span>{sentiment.drivers.length ? sentiment.drivers.join(' · ') : 'No active drivers'}</span>
            </p>
          </>
        ) : (
          <p>No sentiment scorecard yet.</p>
        )}
      </div>

      <div className="tg-macro-block">
        <strong>Market state</strong>
        <p>
          Structure: <span>{data.structure.state}</span>
        </p>
        <p>
          Confidence: <span>{Math.round(data.structure.confidence * 100)}%</span>
        </p>
        <p>{data.structure.reason}</p>
      </div>

      <div className="tg-macro-block">
        <strong>Why price may be moving</strong>
        {sentiment ? (
          <p>{sentiment.movement_explainer}</p>
        ) : (
          <p>No live macro explanation yet.</p>
        )}
      </div>

      <div className="tg-macro-block">
        <strong>Execution pressure</strong>
        {plan ? (
          <>
            <p>
              Plan: <span>{plan.status}</span>
            </p>
            <p>
              TF roles: <span>{plan.bias_tf} / {plan.setup_tf} / {plan.entry_tf}</span>
            </p>
            <p>
              Confirmation: <span>{plan.confirmation_state ?? plan.status}</span>
            </p>
            <p>
              TP basis: <span>{plan.target_basis ?? 'pending trigger'}</span>
            </p>
            <p>
              Feed source: <span>{plan.feed_source ?? data.feed?.feed_source ?? '—'}</span>
            </p>
            <p>
              Live vs entry:{' '}
              <span className={riskDelta != null ? toneClass(riskDelta) : 'tg-muted'}>
                {riskDelta != null ? `${riskDelta >= 0 ? '+' : ''}${riskDelta}` : '—'}
              </span>
            </p>
            {(plan.feed_error || data.feed?.feed_error) && <p>{plan.feed_error ?? data.feed?.feed_error}</p>}
          </>
        ) : (
          <p>No execution plan yet. Wait for a qualifying zone.</p>
        )}
      </div>

      <div className="tg-macro-block">
        <strong>Checklist pulse</strong>
        {plan?.checklist?.length ? (
          <ul className="tg-macro-list">
            {plan.checklist.slice(0, 4).map((item, i) => (
              <li key={`${item.stage}-${item.name}-${i}`}>
                <span className={item.passed ? 'tg-green' : 'tg-red'}>{item.passed ? '✓' : '✗'}</span>
                <span>{item.name}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p>No active checklist yet.</p>
        )}
      </div>

      <div className="tg-macro-block">
        <strong>Latest headlines</strong>
        {macro?.articles?.length ? (
          <ul className="tg-macro-list">
            {macro.articles.slice(0, 3).map((item, i) => (
              <li key={`${item.title}-${i}`}>
                <span className="tg-muted">{item.source ?? item.provider ?? 'news'}</span>
                <span>{item.title}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p>
            No live headlines yet. Add <code>MARKETAUX_API_KEY</code> to enable real-time financial article fetches.
          </p>
        )}
      </div>

      <div className="tg-macro-block">
        <strong>Provider status</strong>
        <p>
          Calendar: <span>{macro?.provider_status?.calendar ?? 'disabled'}</span>
        </p>
        <p>
          Headlines: <span>{macro?.provider_status?.headlines ?? macro?.provider_status?.news ?? 'disabled'}</span>
        </p>
        <p>
          Source: <span>{macro?.provider_status?.selected_headline_provider ?? 'none'}</span>
        </p>
        {macro?.provider_status?.headline_error && <p>{macro.provider_status.headline_error}</p>}
      </div>
    </aside>
  )
}

function PulseCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="tg-macro-card">
      <span className="tg-macro-label">{label}</span>
      <strong>{value}</strong>
      <span className="tg-macro-detail">{detail}</span>
    </div>
  )
}
