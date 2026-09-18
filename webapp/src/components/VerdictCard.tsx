import { useMemo, type ReactNode } from 'react'
import type { RescanMeta, Story, StoryBeat, StoryBeatStatus, Verdict } from '../api/types'
import { advanceStory } from '../lib/story'

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

/**
 * The verdict is the hero. WAIT is amber and wordy because it must persuade
 * patience; ENTER is green and terse because only execution details remain.
 */
export default function VerdictCard({
  verdict,
  generatedAt,
  rescan,
  compact,
  livePrice,
  alertState,
}: {
  verdict: Verdict
  generatedAt?: number
  rescan?: RescanMeta
  compact?: boolean
  /** live tick (or last candle close) used to advance story beats between scans */
  livePrice?: number | null
  /** the monitored alert's state for this pair, which outranks a price compare */
  alertState?: string | null
}) {
  const tone =
    verdict.state === 'ENTER' ? 'enter' : verdict.state === 'STAND_ASIDE' ? 'aside' : 'wait'

  const story = useMemo(
    () => advanceStory(verdict.story, { price: livePrice ?? null, alertState }),
    [verdict.story, livePrice, alertState],
  )

  return (
    <div className={`tg-verdict-row ${compact ? 'tg-verdict-row--compact' : ''}`}>
      <div className={`tg-verdict tg-verdict--${tone}`}>
        <h2>{verdict.headline}</h2>
        {verdict.plan?.instruction && <p className="tg-gate">{verdict.plan.instruction}</p>}
        {generatedAt && <p className="tg-plan-meta">Last analysed: <strong>{fmtDateTime(generatedAt)}</strong></p>}
        {rescan && (
          <p className="tg-plan-meta">
            Next scheduled recheck: <strong>{fmtDateTime(rescan.next_recheck_at)}</strong>
            {' '}· stale after <strong>{fmtDateTime(rescan.stale_after)}</strong>
            {rescan.lower_tf_wakeup_enabled && rescan.lower_tf_wakeup_tf && (
              <> · early wake-up on <strong>{rescan.lower_tf_wakeup_tf}</strong></>
            )}
          </p>
        )}
        {verdict.reasons.map((r, i) => (
          <p key={i}>{r}</p>
        ))}
        {verdict.enter_blocked_by && (
          <p className="tg-gate">🔒 ENTER gated: {verdict.enter_blocked_by}</p>
        )}
      </div>

      {verdict.plan ? (
        <div className={`tg-tiles ${compact ? 'tg-tiles--compact' : ''}`}>
          <Tile label="Planned entry" value={verdict.plan.entry != null ? verdict.plan.entry.toFixed(2) : '—'} />
          <Tile
            label="Stop"
            value={verdict.plan.stop != null ? verdict.plan.stop.toFixed(2) : '—'}
            tone="red"
          />
          <Tile
            label={
              verdict.plan.rr != null ? `Target (${verdict.plan.rr}R)` : 'Target'
            }
            value={verdict.plan.target != null ? verdict.plan.target.toFixed(2) : '—'}
            tone="green"
          />
          <Tile
            label={`Size @ ${verdict.plan.risk_pct}% risk`}
            value={verdict.plan.size != null ? verdict.plan.size.toFixed(2) : '—'}
          />
          {(verdict.plan.bias_tf || verdict.plan.setup_tf || verdict.plan.entry_tf) && (
            <p className="tg-plan-meta">
              {verdict.plan.trader_mode && <>Mode: <strong>{verdict.plan.trader_mode}</strong> · </>}
              Top-down: <strong>{verdict.plan.bias_tf}</strong> bias · <strong>{verdict.plan.setup_tf}</strong> setup · <strong>{verdict.plan.entry_tf}</strong> confirmation
            </p>
          )}
          {(verdict.plan.stop_points != null || verdict.plan.target_points != null) && (
            <p className="tg-plan-meta">
              {verdict.plan.stop_points != null && verdict.plan.stop_pips != null && (
                <>SL distance: <strong>{verdict.plan.stop_points.toFixed(2)} pts / {verdict.plan.stop_pips.toFixed(0)} pips</strong></>
              )}
              {verdict.plan.stop_points != null && verdict.plan.target_points != null && ' · '}
              {verdict.plan.target_points != null && verdict.plan.target_pips != null && (
                <>TP distance: <strong>{verdict.plan.target_points.toFixed(2)} pts / {verdict.plan.target_pips.toFixed(0)} pips</strong></>
              )}
            </p>
          )}
          {(verdict.plan.feed_source || verdict.plan.resolved_symbol) && (
            <p className="tg-plan-meta">
              Feed: <strong>{verdict.plan.feed_source ?? 'unknown'}</strong> · symbol <strong>{verdict.plan.resolved_symbol ?? '—'}</strong>
            </p>
          )}
          {verdict.plan.feed_error && <p className="tg-plan-meta tg-red">{verdict.plan.feed_error}</p>}
          {verdict.plan.target_basis && (
            <p className="tg-plan-meta">
              TP basis: <strong>{verdict.plan.target_basis}</strong>
            </p>
          )}
          {verdict.plan.checklist.length > 0 && (
            <div className="tg-plan-checklist">
              {verdict.plan.checklist.map((item, i) => (
                <div
                  key={`${item.stage}-${item.name}-${i}`}
                  className={`tg-plan-check ${item.passed ? 'tg-plan-check--pass' : 'tg-plan-check--fail'}`}
                >
                  <span className="tg-plan-check-icon">{item.passed ? '✓' : '✗'}</span>
                  <div>
                    <strong>{item.name}</strong>
                    <span>{item.detail}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          {story && <StorySection story={story} compact={compact} />}
          {verdict.plan.notes.length > 0 && (
            <div className="tg-plan-notes">
              {verdict.plan.notes.map((note, i) => (
                <p key={i}>{note}</p>
              ))}
            </div>
          )}
          {verdict.plan.alternatives.length > 0 && (
            <div className="tg-plan-alts">
              <strong>Alternative targets</strong>
              {verdict.plan.alternatives.map((alt, i) => (
                <p key={`${alt.basis}-${i}`}>
                  {alt.basis} @ {alt.price.toFixed(2)} ({alt.rr}R)
                </p>
              ))}
            </div>
          )}
          {verdict.ai_analysis && verdict.ai_analysis.status !== 'disabled' && (
            <div className="tg-ai-card">
              <div className="tg-ai-head">
                <strong>AI analysis</strong>
                <span>{verdict.ai_analysis.provider}</span>
              </div>
              <p className="tg-ai-type">{verdict.ai_analysis.trade_type}</p>
              {compact ? (
                <>
                  <p>{verdict.ai_analysis.reasoning}</p>
                  {verdict.ai_analysis.why_now[0] && <p className="tg-ai-risk">{verdict.ai_analysis.why_now[0]}</p>}
                </>
              ) : (
                <>
                  {verdict.ai_analysis.entry_zone && (
                    <p className="tg-ai-levels">
                      Entry zone: <strong>{verdict.ai_analysis.entry_zone.min.toFixed(2)} – {verdict.ai_analysis.entry_zone.max.toFixed(2)}</strong>
                      {verdict.ai_analysis.stop_loss != null && (
                        <> · SL <strong>{verdict.ai_analysis.stop_loss.toFixed(2)}</strong></>
                      )}
                      {verdict.ai_analysis.take_profit_1 != null && (
                        <> · TP1 <strong>{verdict.ai_analysis.take_profit_1.toFixed(2)}</strong></>
                      )}
                      {verdict.ai_analysis.take_profit_2 != null && (
                        <> · TP2 <strong>{verdict.ai_analysis.take_profit_2.toFixed(2)}</strong></>
                      )}
                    </p>
                  )}
                  <p>{verdict.ai_analysis.reasoning}</p>
                  {verdict.ai_analysis.why_now.length > 0 && (
                    <div className="tg-ai-why">
                      {verdict.ai_analysis.why_now.map((line, i) => (
                        <p key={i}>• {line}</p>
                      ))}
                    </div>
                  )}
                  <p className="tg-ai-risk">Risk note: {verdict.ai_analysis.risk_note}</p>
                </>
              )}
              {verdict.ai_analysis.error && (
                <p className="tg-plan-meta tg-red">Fallback used: {verdict.ai_analysis.error}</p>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="tg-tiles tg-tiles--empty">
          <span>No plan — nothing met the threshold.</span>
          {story && <StorySection story={story} compact={compact} />}
        </div>
      )}
    </div>
  )
}

const MARK: Record<StoryBeatStatus, string> = {
  done: '✓',
  active: '▸',
  pending: '○',
  failed: '✗',
}

/**
 * The walkthrough: where we are, what has to happen next and at what price,
 * what kills it. In the rail the roadmap stays open and the two follow-up
 * chapters collapse — the roadmap is the answer to "what do I watch for".
 */
function StorySection({ story, compact }: { story: Story; compact?: boolean }) {
  if (!story.beats.length && !story.situation.length) return null
  return (
    <section className="tg-story">
      <div className="tg-story-head">
        <strong>How this should play out</strong>
        {story.state === 'failed' && <span className="tg-story-flag">thesis broken intrabar</span>}
      </div>

      {story.situation.map((line, i) => (
        <p key={i} className="tg-story-situation">{line}</p>
      ))}

      {story.beats.length > 0 && (
        <ol className="tg-story-beats">
          {story.beats.map(beat => (
            <Beat key={beat.id} beat={beat} />
          ))}
        </ol>
      )}

      {story.invalidation && (
        <p className={`tg-story-kill ${story.invalidation.hit ? 'tg-story-kill--hit' : ''}`}>
          <strong>Thesis dies if:</strong> {story.invalidation.detail}
        </p>
      )}

      {story.management.length > 0 && (
        <Chapter title="Once you're in" compact={compact}>
          {story.management.map(item => (
            <p key={item.id}>
              <strong>{item.title}</strong> — {item.detail}
            </p>
          ))}
        </Chapter>
      )}

      {story.alternate && (
        <Chapter title="If it goes the other way" compact={compact}>
          <p>{story.alternate.trigger_detail}</p>
          <p>{story.alternate.outcome}</p>
        </Chapter>
      )}
    </section>
  )
}

function Beat({ beat }: { beat: StoryBeat }) {
  return (
    <li
      className={`tg-story-beat tg-story-beat--${beat.status}${beat.optional ? ' tg-story-beat--opt' : ''}`}
    >
      <span className="tg-story-mark">{MARK[beat.status]}</span>
      <div>
        <strong>{beat.title}</strong>
        <span>{beat.detail}</span>
        {beat.provisional && (
          <span className="tg-story-prov">
            trading through — needs the {beat.timeframe ?? 'bar'} close
          </span>
        )}
      </div>
    </li>
  )
}

/** Collapsed in the rail, always open in the full-width card. */
function Chapter({
  title,
  compact,
  children,
}: {
  title: string
  compact?: boolean
  children: ReactNode
}) {
  if (compact) {
    return (
      <details className="tg-story-more">
        <summary>{title}</summary>
        {children}
      </details>
    )
  }
  return (
    <div className="tg-story-chapter">
      <strong>{title}</strong>
      {children}
    </div>
  )
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: 'red' | 'green' }) {
  return (
    <div className="tg-tile">
      <span className="tg-tile-label">{label}</span>
      <strong className={tone ? `tg-${tone}` : undefined}>{value}</strong>
    </div>
  )
}
