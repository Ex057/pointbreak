import { useEffect, useMemo, useRef, useState } from 'react'
import type { Story } from '../api/types'
import { activeIndex, clippyMood, clippyScript, clippyTeaser, type ClippyMood } from '../lib/clippy'

/**
 * The office assistant, 1997 revival.
 *
 * Fixed to the bottom-right of the viewport so it survives any scrolling, and
 * it narrates the storyteller payload one beat at a time. It snaps itself to
 * whichever beat is live whenever the story changes, so glancing at it always
 * answers "what am I waiting for right now?".
 *
 * It cannot be dismissed — the bubble minimises to a teaser, but the character
 * stays on screen. Motion is deliberately slow: it sits in peripheral vision
 * for hours at a time, so anything brisk reads as nagging.
 */
export default function Clippy({ story }: { story: Story | null }) {
  const lines = useMemo(() => clippyScript(story), [story])
  const mood = useMemo(() => clippyMood(story), [story])
  const teaser = useMemo(() => clippyTeaser(story), [story])
  const snapTo = useMemo(() => activeIndex(story, lines), [story, lines])

  const [open, setOpen] = useState(true)
  const [index, setIndex] = useState(snapTo)
  const [nudge, setNudge] = useState(false)
  const lastSnap = useRef(snapTo)
  const lastBeat = useRef<string | null>(null)

  // Follow the story: when the live beat moves, jump the bubble to it and give
  // the character a wiggle so the change is noticeable without being modal.
  useEffect(() => {
    const beatId = lines[snapTo]?.id ?? null
    if (beatId !== lastBeat.current) {
      lastBeat.current = beatId
      setIndex(snapTo)
      setNudge(true)
      // must outlast the nudge keyframes in styles.css
      const id = window.setTimeout(() => setNudge(false), 1800)
      return () => window.clearTimeout(id)
    }
    lastSnap.current = snapTo
  }, [snapTo, lines])

  // Keep the cursor in range when a rescan changes the script length.
  useEffect(() => {
    setIndex(i => (i >= lines.length ? Math.max(0, lines.length - 1) : i))
  }, [lines.length])

  if (!story || !lines.length) return null

  const line = lines[Math.min(index, lines.length - 1)]

  return (
    <aside className={`tg-clippy tg-clippy--${mood} ${nudge ? 'tg-clippy--nudge' : ''}`}>
      {open ? (
        <div className={`tg-clippy-bubble tg-clippy-bubble--${line.tone ?? 'normal'}`}>
          <div className="tg-clippy-bubble-head">
            <span className="tg-clippy-label">{line.label}</span>
            <button
              className="tg-clippy-min"
              onClick={() => setOpen(false)}
              title="Minimise"
              aria-label="Minimise assistant"
            >
              –
            </button>
          </div>

          <p className="tg-clippy-text">{line.text}</p>
          {line.level && <p className="tg-clippy-level">{line.level}</p>}

          <div className="tg-clippy-nav">
            <button
              onClick={() => setIndex(i => Math.max(0, i - 1))}
              disabled={index === 0}
              aria-label="Previous"
            >
              ‹
            </button>
            <span>
              {index + 1} / {lines.length}
            </span>
            <button
              onClick={() => setIndex(i => Math.min(lines.length - 1, i + 1))}
              disabled={index >= lines.length - 1}
              aria-label="Next"
            >
              ›
            </button>
            {index !== snapTo && (
              <button className="tg-clippy-snap" onClick={() => setIndex(snapTo)}>
                back to now
              </button>
            )}
          </div>
        </div>
      ) : (
        <button className="tg-clippy-teaser" onClick={() => setOpen(true)}>
          {teaser}
        </button>
      )}

      <button
        className="tg-clippy-char"
        onClick={() => setOpen(o => !o)}
        aria-label={open ? 'Minimise assistant' : 'Open assistant'}
        title={open ? 'Minimise' : teaser}
      >
        <Paperclip mood={mood} />
        {!open && story.invalidation?.hit && <span className="tg-clippy-badge">!</span>}
      </button>
    </aside>
  )
}

/**
 * An original bent-paperclip character: one spiral stroke, two eyes, two brows.
 * The brow angles do all the emotional work, so mood is a class away.
 */
function Paperclip({ mood }: { mood: ClippyMood }) {
  return (
    <svg className="tg-clip-svg" viewBox="0 0 72 116" role="img" aria-label={`assistant is ${mood}`}>
      <path
        className="tg-clip-body"
        d="M22 46 V88 a13 13 0 0 0 26 0 V32 a9 9 0 0 0 -18 0 V80"
        fill="none"
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <g className="tg-clip-face">
        <ellipse className="tg-clip-eye" cx="27" cy="40" rx="9" ry="10" />
        <ellipse className="tg-clip-eye" cx="47" cy="40" rx="9" ry="10" />
        <circle className="tg-clip-pupil" cx="28.5" cy="41" r="4" />
        <circle className="tg-clip-pupil" cx="48.5" cy="41" r="4" />
        <path className="tg-clip-brow tg-clip-brow--l" d="M19 27 L34 24" strokeWidth="3.5" strokeLinecap="round" />
        <path className="tg-clip-brow tg-clip-brow--r" d="M40 24 L55 27" strokeWidth="3.5" strokeLinecap="round" />
      </g>
    </svg>
  )
}
