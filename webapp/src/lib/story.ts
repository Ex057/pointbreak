import type { Story, StoryBeat, StoryBeatStatus, StoryComparator } from '../api/types'

/**
 * Advance a story's beats against the live tick and the monitor's alert state.
 *
 * The backend status is the baseline and is authoritative at scan time; this
 * only ever moves a beat FORWARD. That invariant is the whole design: a beat
 * that went green must not flicker back to grey because price ticked away from
 * a level it already reached.
 */

export const STATUS_RANK: Record<StoryBeatStatus, number> = {
  pending: 0,
  active: 1,
  done: 2,
  failed: 3,
}

/** Alert states past which nothing is being monitored any more. */
export const TERMINAL_ALERT = new Set(['TARGET', 'STOPPED', 'EXPIRED', 'INVALIDATED'])

// How far through the alert state machine each beat kind is implied to be.
const ALERT_PROGRESS: Record<string, number> = {
  WATCHING: 0,
  TOUCHED: 1,
  SWEPT: 2,
  FILLED: 3,
  TARGET: 3,
  STOPPED: 3,
}
const KIND_REQUIRES: Record<string, number> = {
  reach_zone: 1,
  sweep: 2,
  choch: 3,
  confirmation: 3,
  entry: 3,
}

export interface LiveContext {
  price: number | null
  alertState?: string | null
}

function higher(a: StoryBeatStatus, b: StoryBeatStatus): StoryBeatStatus {
  return STATUS_RANK[b] > STATUS_RANK[a] ? b : a
}

/**
 * What the live price says about a beat, or null for "no opinion".
 *
 * A `close_*` beat never returns `done`: a tick through the level is not a close
 * through it, and a change-of-character beat means nothing without the close.
 * Those surface as `active` + `provisional` until the next scan confirms.
 */
export function evalBeat(
  target: { level: number | null; comparator: StoryComparator | null },
  price: number | null,
): { status: StoryBeatStatus; provisional: boolean } | null {
  const { level, comparator } = target
  if (price == null || level == null || comparator == null) return null
  if (!Number.isFinite(level) || !Number.isFinite(price)) return null
  switch (comparator) {
    case 'at_or_below':
      return price <= level ? { status: 'done', provisional: false } : null
    case 'at_or_above':
      return price >= level ? { status: 'done', provisional: false } : null
    case 'close_above':
      return price > level ? { status: 'active', provisional: true } : null
    case 'close_below':
      return price < level ? { status: 'active', provisional: true } : null
    default:
      return null
  }
}

function fromAlert(beat: StoryBeat, alertState?: string | null): StoryBeatStatus | null {
  if (!alertState) return null
  const reached = ALERT_PROGRESS[alertState]
  const needs = KIND_REQUIRES[beat.kind]
  if (reached == null || needs == null) return null
  return reached >= needs ? 'done' : null
}

function advanceBeat(beat: StoryBeat, live: LiveContext): StoryBeat {
  let status = beat.status
  let provisional = false

  // Alert state outranks a raw price comparison: the monitor saw the bar, we
  // only see the tick.
  const alertStatus = fromAlert(beat, live.alertState)
  if (alertStatus) status = higher(status, alertStatus)

  const priced = evalBeat(beat, live.price)
  if (priced) {
    const merged = higher(status, priced.status)
    provisional = priced.provisional && merged === priced.status && merged !== 'done'
    status = merged
  }

  return status === beat.status && !provisional
    ? beat
    : { ...beat, status, ...(provisional ? { provisional: true } : {}) }
}

/**
 * Exactly one active beat: the first that is neither settled nor optional.
 * Mirrors storyteller._mark_active so the two ends agree on what "active" means.
 */
function markActive(beats: StoryBeat[]): StoryBeat[] {
  let claimed = false
  return beats.map(beat => {
    if (beat.status === 'done' || beat.status === 'failed' || beat.optional) return beat
    if (!claimed) {
      claimed = true
      return beat.status === 'active' ? beat : { ...beat, status: 'active' as const }
    }
    return beat.status === 'pending' ? beat : { ...beat, status: 'pending' as const }
  })
}

export function advanceStory(
  story: Story | null | undefined,
  live: LiveContext,
): Story | null {
  if (!story) return null

  const beats = markActive(story.beats.map(beat => advanceBeat(beat, live)))
  const management = story.management.map(beat => advanceBeat(beat, live))

  let state = story.state
  let invalidation = story.invalidation
  if (invalidation && invalidation.level != null && invalidation.comparator != null) {
    // A provisional (intrabar) cross counts here on purpose — the UI labels it
    // as intrabar rather than pretending the bar has closed.
    const hit = evalBeat(invalidation, live.price)
    if (hit) {
      invalidation = { ...invalidation, hit: true }
      state = 'failed'
    }
  }
  if (live.alertState === 'INVALIDATED' || live.alertState === 'EXPIRED') {
    state = 'failed'
  }

  return { ...story, beats, management, invalidation, state }
}
