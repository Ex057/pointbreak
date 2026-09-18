import { useEffect, useMemo, useRef, useState } from 'react'
import type { PatternCatalogGroup, PatternCatalogItem } from '../api/types'

type HoverCard =
  | {
      type: 'group'
      title: string
      body: string
      color: string
      left: number
      top: number
    }
  | {
      type: 'pattern'
      title: string
      body: string
      color: string
      left: number
      top: number
      validWhen: string
      watchOut: string
      role: string
    }

const ORDER = [
  'reversal_bullish',
  'reversal_bearish',
  'exhaustion_indecision',
  'continuation_momentum',
]

function roleLabel(role: PatternCatalogItem['role']) {
  if (role === 'trigger') return 'entry trigger'
  if (role === 'warning') return 'warning - never an entry'
  return 'context only'
}

export default function PatternChecklist({
  catalog,
  checked,
  onToggle,
  onSetAll,
  showPending,
  onShowPending,
  title = 'Patterns',
  pendingLabel = 'Show pending (unconfirmed)',
  note = 'A pattern is confirmed only after its candles close and the next candle follows through.',
}: {
  catalog: PatternCatalogGroup[]
  checked: Record<string, boolean>
  onToggle: (name: string) => void
  onSetAll: (value: boolean) => void
  showPending: boolean
  onShowPending: (value: boolean) => void
  title?: string
  pendingLabel?: string
  note?: string
}) {
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    reversal_bullish: true,
    reversal_bearish: true,
    exhaustion_indecision: true,
    continuation_momentum: true,
  })
  const [hoverCard, setHoverCard] = useState<HoverCard | null>(null)
  const hoverTimerRef = useRef<number | null>(null)

  const orderedCatalog = useMemo(
    () =>
      [...catalog].sort(
        (a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key),
      ),
    [catalog],
  )

  useEffect(
    () => () => {
      if (hoverTimerRef.current != null) window.clearTimeout(hoverTimerRef.current)
    },
    [],
  )

  function scheduleCard(card: HoverCard) {
    if (hoverTimerRef.current != null) window.clearTimeout(hoverTimerRef.current)
    hoverTimerRef.current = window.setTimeout(() => setHoverCard(card), 150)
  }

  function clearCard() {
    if (hoverTimerRef.current != null) window.clearTimeout(hoverTimerRef.current)
    setHoverCard(null)
  }

  function placeCard(anchor: DOMRect, color: string, body: any): HoverCard {
    const cardWidth = 300
    const gutter = 12
    const wouldOverflow = anchor.right + gutter + cardWidth > window.innerWidth - 8
    const left = wouldOverflow ? Math.max(8, anchor.left - cardWidth - gutter) : anchor.right + gutter
    const top = Math.min(window.innerHeight - 220, Math.max(8, anchor.top))
    return { ...body, color, left, top } as HoverCard
  }

  function toggleGroup(key: string) {
    // groups default OPEN (?? true) so chart-pattern groups aren't hidden
    setOpenGroups(prev => ({ ...prev, [key]: !(prev[key] ?? true) }))
  }

  function setGroup(group: PatternCatalogGroup, value: boolean) {
    group.patterns.forEach(p => {
      if (!!checked[p.name] !== value) onToggle(p.name)
    })
  }

  return (
    <aside className="tg-checklist">
      <div className="tg-checklist-head">
        <strong>{title}</strong>
        <div className="tg-checklist-actions">
          <button onClick={() => onSetAll(true)}>all</button>
          <button onClick={() => onSetAll(false)}>none</button>
        </div>
      </div>
      <p className="tg-checklist-hint">
        Hover a pattern name, or tab to it, to learn what it means, where it matters, and what can make it weak.
      </p>

      {orderedCatalog.length === 0 ? (
        <p className="tg-checklist-empty">No patterns configured for this instrument.</p>
      ) : (
        <div className="tg-checklist-groups">
          {orderedCatalog.map(group => {
            const allOn = group.patterns.length > 0 && group.patterns.every(p => !!checked[p.name])
            const someOn = group.patterns.some(p => !!checked[p.name])
            return (
              <section key={group.key} className="tg-checklist-group">
                <div
                  className="tg-checklist-group-head"
                  onMouseEnter={e => {
                    const rect = e.currentTarget.getBoundingClientRect()
                    scheduleCard(
                      placeCard(rect, group.color, {
                        type: 'group',
                        title: group.label,
                        body: group.blurb,
                      }),
                    )
                  }}
                  onMouseLeave={clearCard}
                >
                  <button
                    className="tg-checklist-group-toggle"
                    onClick={() => toggleGroup(group.key)}
                    aria-expanded={openGroups[group.key] ?? true}
                  >
                    <span className="tg-checklist-group-dot" style={{ background: group.color }} />
                    <span>{group.label}</span>
                    <span className="tg-checklist-group-badge">({group.count_on_chart} on chart)</span>
                  </button>
                  <input
                    type="checkbox"
                    checked={allOn}
                    ref={el => {
                      if (el) el.indeterminate = !allOn && someOn
                    }}
                    onChange={e => setGroup(group, e.target.checked)}
                    aria-label={`Toggle all ${group.label} patterns`}
                  />
                </div>
                {(openGroups[group.key] ?? true) && (
                  <ul className="tg-checklist-list">
                    {group.patterns.map(pattern => (
                      <li key={pattern.name}>
                        <label
                          className="tg-checklist-pattern"
                          onMouseEnter={e => {
                            const rect = e.currentTarget.getBoundingClientRect()
                            scheduleCard(
                              placeCard(rect, group.color, {
                                type: 'pattern',
                                title: pattern.education.meaning,
                                body: pattern.education.story,
                                validWhen: pattern.education.valid_when,
                                watchOut: pattern.education.watch_out,
                                role: roleLabel(pattern.role),
                              }),
                            )
                          }}
                          onMouseLeave={clearCard}
                          onFocus={e => {
                            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                            scheduleCard(
                              placeCard(rect, group.color, {
                                type: 'pattern',
                                title: pattern.education.meaning,
                                body: pattern.education.story,
                                validWhen: pattern.education.valid_when,
                                watchOut: pattern.education.watch_out,
                                role: roleLabel(pattern.role),
                              }),
                            )
                          }}
                          onBlur={clearCard}
                          tabIndex={0}
                          >
                            <input
                              type="checkbox"
                              checked={!!checked[pattern.name]}
                              onChange={() => onToggle(pattern.name)}
                            />
                          <span className="tg-checklist-pattern-name">{pattern.name}</span>
                          <span className="tg-checklist-pattern-info" aria-hidden="true" title="Hover for learning notes">
                            i
                          </span>
                          <span className="tg-checklist-pattern-count">{pattern.count_on_chart}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )
          })}
        </div>
      )}

      <label className="tg-checklist-pending">
        <input type="checkbox" checked={showPending} onChange={e => onShowPending(e.target.checked)} />
        <span>{pendingLabel}</span>
      </label>
      <p className="tg-checklist-note">{note}</p>

      {hoverCard && (
        <div
          className="tg-pattern-hover-card"
          style={{ left: hoverCard.left, top: hoverCard.top, borderLeftColor: hoverCard.color }}
        >
          <strong>{hoverCard.title}</strong>
          <p>{hoverCard.body}</p>
          {hoverCard.type === 'pattern' && (
            <>
              <div className="tg-pattern-hover-chip">{hoverCard.role}</div>
              <div className="tg-pattern-hover-row">
                <span>Valid when:</span>
                <b>{hoverCard.validWhen}</b>
              </div>
              <div className="tg-pattern-hover-row">
                <span>Watch out:</span>
                <b>{hoverCard.watchOut}</b>
              </div>
            </>
          )}
        </div>
      )}
    </aside>
  )
}
