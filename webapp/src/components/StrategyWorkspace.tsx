import type { AiAnalysisResponse, ScanResult } from '../api/types'
import { checklistStatus } from '../lib/decisionMap'

function fmt(value?: number | null) {
  return value == null ? '—' : value.toFixed(2)
}

export default function StrategyWorkspace({
  data,
  analysis,
  loading,
  onRefresh,
}: {
  data: ScanResult
  analysis: AiAnalysisResponse | null
  loading: boolean
  onRefresh: () => void
}) {
  const plan = data.verdict.plan
  const deep = analysis?.analysis
  const required = plan?.checklist.filter(item => item.required !== false) ?? []
  const blocking = required.filter(item => ['pending', 'failed'].includes(checklistStatus(item)))
  const passed = required.filter(item => checklistStatus(item) === 'passed')
  const outlook = plan?.side === 'long'
    ? 'Potential buy setup'
    : plan?.side === 'short'
      ? 'Potential sell setup'
      : 'No directional setup'

  return (
    <section className="tg-strategy-workspace">
      <div className="tg-strategy-head">
        <div>
          <span className="tg-eyebrow">Strategy workspace</span>
          <h2>{data.pair_display} · {outlook}</h2>
          <p>Analysis is separated from execution: this section explains the thesis, the missing evidence, and how the setup should be improved.</p>
        </div>
        <button className="tg-rescan" onClick={onRefresh} disabled={loading}>
          {loading ? 'Reviewing…' : 'Refresh strategy review'}
        </button>
      </div>

      <div className="tg-strategy-grid">
        <article className="tg-analysis-card">
          <span className="tg-eyebrow">Current outlook</span>
          <h3>{plan?.instruction ?? data.verdict.headline}</h3>
          <p>{deep?.reasoning ?? data.verdict.reasons[0] ?? 'No setup has cleared the scanner threshold.'}</p>
          <dl>
            <div><dt>Entry zone</dt><dd>{plan ? `${fmt(plan.zone_min ?? plan.entry)} – ${fmt(plan.zone_max ?? plan.entry)}` : '—'}</dd></div>
            <div><dt>Confirmation</dt><dd>{plan?.entry_tf ?? '—'}</dd></div>
            <div><dt>Target</dt><dd>{fmt(plan?.target)}</dd></div>
            <div><dt>Structure</dt><dd>{data.structure.state}</dd></div>
          </dl>
        </article>

        <article className="tg-analysis-card">
          <span className="tg-eyebrow">Decision gates</span>
          <h3>{passed.length} passed · {blocking.length} still blocking</h3>
          {(plan?.checklist ?? []).length ? (
            <ul className="tg-analysis-list">
              {plan!.checklist.map((item, index) => {
                const status = checklistStatus(item)
                const icon = status === 'passed' ? '✓' : status === 'failed' ? '✗' : status === 'pending' ? '○' : 'i'
                return (
                  <li key={`${item.stage}-${index}`} className={`is-${status}`}>
                    <strong>{icon} {item.name}</strong>
                    <span>{item.detail}</span>
                  </li>
                )
              })}
            </ul>
          ) : <p>No candidate has reached the gate pipeline yet.</p>}
        </article>

        <article className="tg-analysis-card">
          <span className="tg-eyebrow">What must improve</span>
          <h3>{deep?.agreement ? `${deep.agreement.state} · ${deep.agreement.confidence} evidence` : 'Evidence review'}</h3>
          {deep?.what_is_missing?.length ? (
            <ul className="tg-analysis-list">
              {deep.what_is_missing.map((item, index) => <li key={index} className="is-block"><span>{item}</span></li>)}
            </ul>
          ) : (
            <p>{plan?.status === 'ENTER_READY' ? 'No deterministic gate is missing. Continue validating the setup across recorded examples.' : 'Collect more comparable setups before assigning confidence.'}</p>
          )}
          {deep?.history_check && <p className="tg-analysis-evidence">{deep.history_check.summary}</p>}
          <p className="tg-analysis-risk">{deep?.risk_note ?? 'The outlook remains a scenario until its confirmation condition closes.'}</p>
        </article>
      </div>
    </section>
  )
}
