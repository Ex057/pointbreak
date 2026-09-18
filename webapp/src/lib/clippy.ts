import type { Story, StoryBeat } from '../api/types'

/**
 * Turns a story into something a 1997 office assistant would say.
 *
 * The script is a flat, ordered list so the bubble is just a cursor over an
 * array — and so "jump to whatever the trader must watch right now" is a single
 * index lookup (`activeIndex`) rather than a tree walk.
 */

export type ClippyMood = 'idle' | 'watching' | 'excited' | 'alarmed' | 'asleep'

export interface ClippyLine {
  id: string
  /** short label shown above the text, e.g. "Step 3 of 5 · now" */
  label: string
  text: string
  /** the price this line hangs on, pre-formatted, if any */
  level?: string | null
  tone?: 'normal' | 'good' | 'bad' | 'watch'
}

const MARK: Record<string, string> = { done: '✓', active: '▸', pending: '○', failed: '✗' }

function fmtLevel(beat: StoryBeat): string | null {
  if (beat.level == null || !Number.isFinite(beat.level)) return null
  const tf = beat.timeframe ? `${beat.timeframe} ` : ''
  switch (beat.comparator) {
    case 'close_above':
      return `${tf}close above ${beat.level.toLocaleString(undefined, { maximumFractionDigits: 5 })}`
    case 'close_below':
      return `${tf}close below ${beat.level.toLocaleString(undefined, { maximumFractionDigits: 5 })}`
    case 'at_or_below':
      return `price at or below ${beat.level.toLocaleString(undefined, { maximumFractionDigits: 5 })}`
    case 'at_or_above':
      return `price at or above ${beat.level.toLocaleString(undefined, { maximumFractionDigits: 5 })}`
    default:
      return null
  }
}

function opener(story: Story): string {
  const side = story.side ?? 'setup'
  switch (story.state) {
    case 'blocked':
      return "It looks like the feed is arguing with itself. I can't measure a single level off a price I don't trust, so nothing is armed."
    case 'no_setup':
      return "It looks like you're waiting for a setup! Nothing has cleared the filters yet — here is the nearest thing worth watching."
    case 'in_trade':
      return `It looks like you're taking a ${side}! Every gate passed, so from here it is management, not analysis.`
    case 'failed':
      return `It looks like this ${side} just broke intrabar. Read the invalidation before you do anything clever.`
    default:
      return `It looks like you're planning a ${side}! Here is how it should play out — and what has to happen first.`
  }
}

export function clippyMood(story: Story | null): ClippyMood {
  if (!story) return 'asleep'
  switch (story.state) {
    case 'failed':
      return 'alarmed'
    case 'in_trade':
      return 'excited'
    case 'blocked':
      return 'asleep'
    case 'no_setup':
      return 'idle'
    default:
      return 'watching'
  }
}

export function clippyScript(story: Story | null): ClippyLine[] {
  if (!story) return []
  const lines: ClippyLine[] = [
    { id: 'opener', label: 'The situation', text: opener(story), tone: 'normal' },
  ]

  story.situation.forEach((text, i) => {
    lines.push({ id: `situation-${i}`, label: 'The situation', text, tone: 'normal' })
  })

  const total = story.beats.length
  story.beats.forEach((beat, i) => {
    const position = `Step ${i + 1} of ${total}`
    const suffix =
      beat.status === 'active'
        ? ' · watching now'
        : beat.status === 'done'
          ? ' · done'
          : beat.status === 'failed'
            ? ' · failed'
            : beat.optional
              ? ' · optional'
              : ''
    lines.push({
      id: beat.id,
      label: `${MARK[beat.status] ?? '○'} ${position}${suffix}`,
      text: `${beat.title}. ${beat.detail}`,
      level: beat.provisional
        ? `trading through — still needs the ${beat.timeframe ?? 'bar'} close`
        : fmtLevel(beat),
      tone:
        beat.status === 'failed'
          ? 'bad'
          : beat.status === 'done'
            ? 'good'
            : beat.status === 'active'
              ? 'watch'
              : 'normal',
    })
  })

  if (story.invalidation) {
    lines.push({
      id: 'invalidation',
      label: story.invalidation.hit ? 'Thesis broken' : 'Thesis dies if',
      text: story.invalidation.detail,
      level: story.invalidation.hit ? 'this has just triggered intrabar' : null,
      tone: 'bad',
    })
  }

  story.management.forEach(beat => {
    lines.push({
      id: beat.id,
      label: "Once you're in",
      text: `${beat.title}. ${beat.detail}`,
      level: fmtLevel(beat),
      tone: 'normal',
    })
  })

  if (story.alternate) {
    lines.push({
      id: 'alternate-trigger',
      label: 'If it goes the other way',
      text: story.alternate.trigger_detail,
      tone: 'watch',
    })
    lines.push({
      id: 'alternate-outcome',
      label: 'If it goes the other way',
      text: story.alternate.outcome,
      tone: 'watch',
    })
  }

  return lines
}

/**
 * Where the trader actually is in the story — the line the bubble should snap
 * to when the story changes. Prefers a live invalidation, then the active beat.
 */
export function activeIndex(story: Story | null, lines: ClippyLine[]): number {
  if (!story || !lines.length) return 0
  if (story.invalidation?.hit) {
    const hit = lines.findIndex(l => l.id === 'invalidation')
    if (hit >= 0) return hit
  }
  const active = story.beats.find(b => b.status === 'active')
  if (active) {
    const idx = lines.findIndex(l => l.id === active.id)
    if (idx >= 0) return idx
  }
  return 0
}

/** One-line nudge for the collapsed bubble. */
export function clippyTeaser(story: Story | null): string {
  if (!story) return 'Nothing to analyse yet.'
  if (story.invalidation?.hit) return 'Heads up — the thesis just broke.'
  const active = story.beats.find(b => b.status === 'active')
  if (active) return active.title
  if (story.state === 'in_trade') return 'All gates passed — manage it.'
  return story.title || 'Here is how this should play out.'
}
