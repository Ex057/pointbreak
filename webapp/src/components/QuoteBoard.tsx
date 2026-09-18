import { useMemo, useState } from 'react'
import type { MarketQuote } from '../api/types'

function fmtPrice(value: number | null) {
  if (value == null) return '—'
  if (Math.abs(value) >= 1000) return value.toFixed(2)
  if (Math.abs(value) >= 10) return value.toFixed(3)
  return value.toFixed(5)
}

export default function QuoteBoard({
  quotes,
  selected,
  generatedAt,
  onSelect,
}: {
  quotes: MarketQuote[]
  selected: string
  generatedAt?: number | null
  onSelect: (pair: string) => void
}) {
  const [category, setCategory] = useState('all')
  const categories = useMemo(
    () => ['all', ...Array.from(new Set(quotes.map(item => item.category)))],
    [quotes],
  )
  const visible = category === 'all' ? quotes : quotes.filter(item => item.category === category)

  return (
    <section className="tg-quotes">
      <div className="tg-quotes-head">
        <div>
          <strong>All-market quote monitor</strong>
          <span>Live quotes stay active across the catalog; select one to open its full analysis.</span>
        </div>
        <div className="tg-quotes-tools">
          <select value={category} onChange={event => setCategory(event.target.value)}>
            {categories.map(item => <option key={item} value={item}>{item}</option>)}
          </select>
          {generatedAt && <span>updated {new Date(generatedAt * 1000).toLocaleTimeString()}</span>}
        </div>
      </div>
      <div className="tg-quote-grid">
        {visible.map(item => {
          const change = item.price != null && item.ohlc?.open
            ? ((item.price - item.ohlc.open) / item.ohlc.open) * 100
            : null
          return (
            <button
              key={item.pair}
              className={`tg-quote ${selected === item.pair ? 'tg-quote--selected' : ''}`}
              onClick={() => onSelect(item.pair)}
            >
              <span className="tg-quote-name">
                <i className={`tg-quote-dot ${item.price != null && !item.stale ? 'tg-quote-dot--live' : ''}`} />
                {item.display}
              </span>
              <strong>{fmtPrice(item.price)}</strong>
              <span className={change == null ? 'tg-muted' : change >= 0 ? 'tg-green' : 'tg-red'}>
                {change == null ? item.source : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`}
              </span>
              <small>{item.realtime ? 'live' : item.stale ? 'delayed' : item.source}</small>
            </button>
          )
        })}
      </div>
    </section>
  )
}
