import type { MarketStructure, StructureState } from '../api/types'

const META: Record<StructureState, { label: string; cls: string; glyph: string }> = {
  UPTREND: { label: 'Uptrend', cls: 'up', glyph: '▲' },
  DOWNTREND: { label: 'Downtrend', cls: 'down', glyph: '▼' },
  RANGE: { label: 'Range', cls: 'range', glyph: '↔' },
  CONSOLIDATION: { label: 'Consolidation', cls: 'consol', glyph: '◇' },
  UNDETERMINED: { label: 'Undetermined', cls: 'undet', glyph: '·' },
}

/**
 * The market-structure read — swing-sequence based (HH/HL/LH/LL), richer than
 * the engine's 3-way bias. Hovering shows the reasoning.
 */
export default function StructureBadge({ structure }: { structure: MarketStructure }) {
  const m = META[structure.state] ?? META.UNDETERMINED
  const pct = Math.round(structure.confidence * 100)
  return (
    <span className={`tg-struct tg-struct--${m.cls}`} title={structure.reason}>
      <span className="tg-struct-glyph">{m.glyph}</span>
      {m.label}
      <span className="tg-struct-conf">{pct}%</span>
    </span>
  )
}
