// Toggleable chart layers: S/R lines, the two shaded-zone types, and one key
// per moving average (each individually toggleable).
export type LayerKey =
  | 'sr'
  | 'supply_demand'
  | 'fvg'
  | 'decision_map'
  | 'ema9'
  | 'ema21'
  | 'ema50'
  | 'sma200'

export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  color?: string
  borderColor?: string
  wickColor?: string
}

export interface Zone {
  layer: string
  level_min: number
  level_max: number
  level_mid: number
  kind: string
  priority: 'high' | 'medium' | 'low'
  priority_score: number
  priority_reason: string
  touches?: number
  time?: number
  end_time?: number
  start_time?: number // FVG: first candle of the 3-candle gap
  fill_time?: number | null // FVG: bar where the gap got filled (null if still open)
  top?: number
  bottom?: number
  fresh?: boolean
  filled?: boolean
}

export interface MASeries {
  layer: 'moving_averages'
  name: string // e.g. "EMA 9", "SMA 200"
  period: number
  kind: 'ema' | 'sma'
  points: { time: number; value: number }[]
}

export interface PairOption {
  id: string
  display: string
  code: string
  category: string
  live: boolean
  sanity_min?: number | null
}

export interface HealthResponse {
  status: string
  data_source: string
  default_pair: string
  default_setup_tf?: string
  default_trader_mode?: string
  default_ai_provider?: string
  trader_modes?: Record<string, { bias_tf: string; setup_tf: string; entry_tf: string }>
  ai_providers?: Record<string, { label: string; free: boolean; configured: boolean; status: string; detail: string; model?: string }>
  feed_status: {
    connected: boolean
    mode: string
    resolved: number
    requested?: number
    last_msg?: number
    reconnects?: number
    error?: string
  }
  pairs: PairOption[]
  timeframes: string[]
}

export interface ReconnaissanceLevel {
  price: number
  type: string
  priority?: 'high' | 'medium' | 'low' | null
  distance_pct: number
}

export interface ReconnaissanceLiquidityLevel {
  id: 'PDH' | 'PDL' | 'PWH' | 'PWL'
  label: string
  price: number
  period: 'day' | 'week'
  side: 'high' | 'low'
  source_epoch: number
}

export interface ReconnaissanceTimeframe {
  timeframe: '1W' | '4H' | '1H' | '30m' | '15m'
  available: boolean
  bars: number
  sample_confidence: number
  last_complete_epoch: number | null
  price: number | null
  structure: string
  structure_confidence: number
  reason: string
  bias: string
  direction_score: number
  volatility: {
    atr_pct: number | null
    percentile: number | null
    regime: 'low' | 'normal' | 'high' | 'unknown'
  }
  levels: {
    support: ReconnaissanceLevel | null
    resistance: ReconnaissanceLevel | null
  }
  candidate: {
    side: string | null
    source: string | null
    entry: number | null
    rr: number | null
  } | null
  error: string | null
}

export interface ReconnaissanceResponse {
  pair: string
  pair_display: string
  status: 'ready' | 'partial' | 'unavailable'
  generated_at: number
  next_refresh_at: number
  window_days: number
  cached: boolean
  summary: {
    stance: 'bullish_alignment' | 'bullish_lean' | 'mixed' | 'bearish_lean' | 'bearish_alignment' | 'unavailable'
    score: number
    confidence: number
    headline: string
    change_pct_30d: number | null
    high_30d: number | null
    low_30d: number | null
    range_position: number | null
    volatility: 'low' | 'normal' | 'high' | 'unknown'
  }
  liquidity: {
    levels: ReconnaissanceLiquidityLevel[]
  }
  timeframes: ReconnaissanceTimeframe[]
  conflicts: string[]
  warnings: string[]
}

export interface Pattern {
  time: number
  start_time: number
  end_time: number
  name: string
  direction: string
  price: number
  strength: number
  at_zone: boolean
  span: number
  confirmed: boolean
  status: 'confirmed' | 'pending'
  available: boolean
  group?: string
  role?: 'trigger' | 'warning' | 'context'
  education?: {
    meaning: string
    story: string
    valid_when: string
    watch_out: string
  }
}

export interface PatternCatalogItem {
  name: string
  role: 'trigger' | 'warning' | 'context'
  count_on_chart: number
  education: {
    meaning: string
    story: string
    valid_when: string
    watch_out: string
  }
}

export interface PatternCatalogGroup {
  key: string
  label: string
  color: string
  blurb: string
  count_on_chart: number
  patterns: PatternCatalogItem[]
}

// ---- Chart / structure patterns (multi-bar: flags, triangles, H&S, …) ----
export interface ChartPatternLine {
  t0: number
  y0: number
  t1: number
  y1: number
  kind: string // resistance | support | neckline
}

export interface ChartPatternPivot {
  time: number
  price: number
  label: string // LS/H/RS, 1/2/3, pole, base…
}

export interface ChartPattern {
  name: string
  category: 'bullish' | 'bearish' | 'range'
  direction: 'bullish' | 'bearish' | 'neutral'
  status: 'forming' | 'confirmed'
  pivots: ChartPatternPivot[]
  lines: ChartPatternLine[]
  region: { start_time: number; end_time: number; top: number; bottom: number } | null
  breakout_time: number | null
  target: number | null
  stop: number | null
  score: number
  role?: 'trigger' | 'warning' | 'context'
  education?: { meaning: string; story: string; valid_when: string; watch_out: string }
}

export interface Entry {
  side: 'long' | 'short'
  source: string
  zone_min: number
  zone_max: number
  entry_price: number
  eta_bars: number
  t_future: number
  confluence: number
  with_bias: boolean
  score: number
  stop: number
  target: number
  rr: number
  note: string
}

export interface NewsEvent {
  title: string
  currency: string
  impact: string
  minutes_away: number
  provider?: string
  veto_active?: boolean
  articles?: NewsArticle[]
}

export interface NewsArticle {
  title: string
  description?: string | null
  url?: string | null
  source?: string | null
  published_at?: string | null
  provider?: string | null
  provider_sentiment?: number | null
}

export interface NewsSentiment {
  score: number
  label: 'bullish' | 'neutral' | 'bearish'
  confidence: 'low' | 'medium' | 'high'
  drivers: string[]
  movement_explainer: string
  headline_count: number
  event_count: number
  updated_at: number
}

export interface PriceTick {
  pair: string
  source: 'deriv' | 'yahoo' | 'none'
  price: number | null
  epoch?: number
  received_at?: number | null
  age?: number
  received_age?: number
  stale?: boolean
  realtime?: boolean
  quote_kind?: 'last' | 'mid' | 'bid' | 'ask'
  fallback_reason?: string | null
  warming?: boolean
  ohlc?: {
    open: number
    high: number
    low: number
    close: number
    time?: number | null
  } | null
}

export interface MarketQuote {
  pair: string
  display: string
  category: string
  available: boolean
  price: number | null
  source: 'deriv' | 'yahoo' | 'candle' | 'none'
  epoch?: number | null
  stale: boolean
  realtime: boolean
  ohlc?: PriceTick['ohlc']
}

export interface QuotesResponse {
  generated_at: number
  quotes: MarketQuote[]
}

export type StoryBeatStatus = 'done' | 'active' | 'pending' | 'failed'
export type ChecklistStatus = 'passed' | 'pending' | 'failed' | 'informational'

export interface ChecklistItem {
  stage: string
  name: string
  passed: boolean
  detail: string
  status?: ChecklistStatus
  required?: boolean
}
export type StoryBeatKind =
  | 'reach_zone'
  | 'sweep'
  | 'choch'
  | 'confirmation'
  | 'entry'
  | 'manage'
  | 'invalidate'
export type StoryComparator = 'at_or_below' | 'at_or_above' | 'close_above' | 'close_below'

/**
 * One step in the walkthrough. `level` + `comparator` are what let the client
 * advance a beat between scans (see lib/story.ts) — a beat without a level is
 * narrative only and never ticks on its own.
 */
export interface StoryBeat {
  id: string
  kind: StoryBeatKind
  title: string
  detail: string
  status: StoryBeatStatus
  level: number | null
  comparator: StoryComparator | null
  timeframe: string | null
  optional?: boolean
  /** client-side only: price is through the level but the bar has not closed */
  provisional?: boolean
}

export interface StoryInvalidation {
  level: number | null
  comparator: StoryComparator | null
  timeframe: string | null
  detail: string
  /** client-side only */
  hit?: boolean
}

export interface StoryAlternate {
  trigger_detail: string
  level: number | null
  comparator: StoryComparator | null
  outcome: string
}

export interface Story {
  version: number
  state: 'roadmap' | 'in_trade' | 'no_setup' | 'blocked' | 'failed'
  status: string
  side: 'long' | 'short' | null
  title: string
  situation: string[]
  beats: StoryBeat[]
  invalidation: StoryInvalidation | null
  management: StoryBeat[]
  alternate: StoryAlternate | null
}

export interface Verdict {
  state: 'WAIT' | 'ENTER' | 'STAND_ASIDE'
  headline: string
  reasons: string[]
  plan: {
    status: 'ENTER_READY' | 'WAITING' | 'DISCARDED'
    side: string | null
    source?: string | null
    entry: number | null
    zone_min?: number | null
    zone_max?: number | null
    stop: number | null
    target: number | null
    rr: number | null
    size: number | null
    risk_pct: number
    trader_mode?: string
    target_basis: string | null
    stop_points?: number | null
    stop_pips?: number | null
    target_points?: number | null
    target_pips?: number | null
    notes: string[]
    alternatives: { price: number; basis: string; rr: number }[]
    checklist: ChecklistItem[]
    confluence?: number
    score?: number
    bias_tf?: string
    setup_tf?: string
    entry_tf?: string
    feed_source?: string
    resolved_symbol?: string
    price_valid?: boolean
    feed_error?: string | null
    confirmation_state?: string
    instruction?: string
  } | null
  news: NewsEvent | null
  macro?: {
    event: NewsEvent | null
    articles: NewsArticle[]
    sentiment?: NewsSentiment
    provider_status: {
      calendar: string
      headlines: string
      news?: string
      selected_headline_provider?: string
      headline_error?: string
    }
    updated_at: number
  }
  ai_analysis?: {
    provider: string
    status: string
    trade_type: string
    entry_zone?: { min: number; max: number } | null
    stop_loss?: number | null
    take_profit_1?: number | null
    take_profit_2?: number | null
    reasoning: string
    why_now: string[]
    risk_note: string
    generated_at: number
    error?: string
  }
  enter_blocked_by?: string
  /** Optional so a stale backend or a storyteller fallback never breaks render. */
  story?: Story | null
}

export interface AiAnalysisResponse {
  pair: string
  pair_display: string
  timeframe: string
  trader_mode: string
  generated_at: number
  force_refresh: boolean
  analysis: {
    provider: string
    provider_label?: string
    status: string
    trade_type: string
    entry_zone?: { min: number; max: number } | null
    stop_loss?: number | null
    take_profit_1?: number | null
    take_profit_2?: number | null
    reasoning: string
    why_now: string[]
    what_is_missing: string[]
    risk_note: string
    generated_at: number
    error?: string
    fallback_used?: boolean
    detection_gaps?: {
      day: string
      blind_count: number
      biggest_blind_r: number
      top: MissedMove[]
    }
    agreement: {
      state: 'agrees' | 'cautious' | 'disagrees'
      confidence: 'low' | 'medium' | 'high'
    }
    history_check: {
      summary: string
      counts: {
        closed_sample: number
        targets: number
        stopped: number
        waiting: number
        retired: number
        discarded: number
      }
      win_rate: number | null
      sample_size: number
    }
    trade_framing: {
      label: string
      entry_zone?: { min: number; max: number } | null
      stop_loss?: number | null
      take_profit_1?: number | null
      take_profit_2?: number | null
    }
    learned_score?: {
      status: string
      quality_score: number | null
      fill_probability: number | null
      target_probability: number | null
      retire_risk: number | null
      model_confidence: 'low' | 'medium' | 'high'
      model_version?: string | null
      training_sample_count?: number
      segment_count?: number
      segment_key?: string
      history_window?: { start: number | null; end: number | null }
    } | null
    learning_status?: {
      pair: string
      enabled: boolean
      model_kind: string
      active_model_version?: string | null
      active_model_status?: string | null
      last_retrained?: number | null
      training_window: { start: number | null; end: number | null }
      sample_count: number
      holdout_size: number
      promotion_result?: string | null
      promotion_reason?: string | null
      metrics: {
        holdout_size?: number
        candidate_mae?: number | null
        baseline_mae?: number | null
        reason?: string
      }
      summary: {
        outcomes?: Record<string, number>
        targets?: number
        stopped?: number
        retired?: number
        discarded?: number
        expired?: number
      }
      history: Array<{
        version: string
        status: string
        trained_at: number
        sample_count: number
        holdout_size: number
        promotion_reason?: string | null
        metrics: {
          holdout_size?: number
          candidate_mae?: number | null
          baseline_mae?: number | null
          reason?: string
        }
      }>
    } | null
  }
}

export interface ResearchFrameStatus {
  rows: number
  valid_rows: number
  off_grid: number
  invalid_ohlc: number
  gaps: number
  days: number
  sources: Record<string, number>
  selected_source: string
  first_epoch?: number | null
  last_epoch?: number | null
}

export interface ResearchDataStatus {
  pair: string
  model_kind: string
  research_only: boolean
  label_horizon_bars: number
  journal_rows: number
  journal_days: number
  modes: Array<{
    trader_mode: string
    frames: Record<string, ResearchFrameStatus>
    blockers: string[]
  }>
}

export interface ResearchHistorySource {
  rows: number
  first_epoch: number | null
  last_epoch: number | null
  usable_for_forecast: boolean
}

export interface ResearchDataJob {
  id: number
  pair: string
  timeframe: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  source?: 'deriv' | 'yahoo' | null
  years: number
  written: number
  message?: string | null
  result: Record<string, unknown>
  error?: string | null
  created_at: number
  started_at?: number | null
  completed_at?: number | null
}

export interface ResearchHistory {
  history: {
    pair: string
    timeframe: string
    default_years: number
    minimum_candles: number
    selected_source: 'deriv' | 'yahoo' | null
    sources: Record<'deriv' | 'yahoo', ResearchHistorySource>
    yahoo_symbol: string
    note?: string | null
  }
  active_job?: ResearchDataJob | null
  jobs: ResearchDataJob[]
}

export interface PriceForecastRun {
  id: number
  pair: string
  timeframe: string
  source: 'deriv' | 'yahoo'
  status: 'queued' | 'running' | 'completed' | 'inconclusive' | 'insufficient_data' | 'failed'
  metrics: Record<string, { mse: number; mae: number; rmse: number; directional_accuracy: number; test_points: number; up_probability_brier?: number; up_probability_log_loss?: number; up_probability_roc_auc?: number | null }>
  points: Array<{ epoch: number; actual: number; last_close: number; lightgbm?: number; hist_gradient_boosting?: number; probability_up?: number; last_close_probability_up?: number }>
  data: {
    source_note?: string | null
    model_name?: string | null
    future_forecast?: {
      epoch: number
      as_of_epoch: number
      last_close: number
      prediction: number
      direction: 'up' | 'down' | 'flat'
      bias: 'BUY BIAS' | 'SELL BIAS' | 'NO CLEAR EDGE'
      probability_up: number
      probability_down: number
      model: string
      intervals: Record<'50' | '90', { lower: number; upper: number }>
      calibration_samples: number
      stale_hours: number
    }
    feature_importance?: Array<{ feature: string; gain: number }>
    model_error?: string | null
    error?: string | null
    [key: string]: unknown
  }
  error?: string | null
  created_at: number
  completed_at?: number | null
}

export interface ResearchModelMetrics {
  brier: number
  log_loss: number
  roc_auc?: number | null
  pr_auc?: number | null
}

export interface ResearchModeResult {
  trader_mode: string
  status: 'insufficient_data' | 'inconclusive' | 'qualified_for_shadow'
  sample_count: number
  independent_days: number
  positive_count: number
  negative_count: number
  censored_count: number
  metrics: Record<string, ResearchModelMetrics> & {
    qualification?: {
      best_baseline_brier?: number | null
      lightgbm_relative_brier_improvement?: number | null
      lightgbm_minus_baseline_brier_day_bootstrap_95ci?: number[] | null
      required_improvement?: number
    }
    lightgbm_net_r_after_spread?: number
  }
  folds: Array<{
    fold: number
    train_samples: number
    test_samples: number
    train_end: string
    test_start: string
    test_end: string
    metrics: Record<string, ResearchModelMetrics>
  }>
  feature_importance: Array<{ feature: string; gain: number }>
  predictions?: Array<{
    epoch: number
    day: string
    fold: number
    label: 0 | 1
    outcome: string
    rr?: number | null
    risk_distance?: number | null
    predictions: Record<string, number>
  }>
  blockers: string[]
}

export interface ResearchRun {
  id: number
  pair: string
  modes: string[]
  status: 'queued' | 'running' | 'completed' | 'failed'
  config: Record<string, unknown>
  summary: { research_only?: boolean; qualified_modes?: string[]; inconclusive_modes?: string[] }
  error?: string | null
  data_fingerprint?: string | null
  created_at: number
  started_at?: number | null
  completed_at?: number | null
  results?: ResearchModeResult[]
}

export interface RescanMeta {
  last_recheck_reason?: string | null
  next_recheck_at: number
  stale_after: number
  entry_tf_seconds: number
  lower_tf_wakeup_enabled: boolean
  lower_tf_wakeup_tf?: string | null
  lower_tf_next_recheck_at?: number | null
}

export type StructureState =
  | 'UPTREND'
  | 'DOWNTREND'
  | 'RANGE'
  | 'CONSOLIDATION'
  | 'UNDETERMINED'

export interface MarketStructure {
  state: StructureState
  confidence: number
  reason: string
  range_high: number | null
  range_low: number | null
  swing_summary: { highs: number[]; lows: number[] }
}

export interface MonitorStatus {
  pair: string | null
  pair_display: string | null
  trader_mode?: string
  timeframe: string
  bias_tf?: string
  setup_tf?: string
  entry_tf?: string
  running: boolean
  active_alerts: number
  error: string | null
}

export interface AlertEvent {
  id: number
  alert_id: number
  state: string
  ts: number
  price: number
  reason: string
}

export interface AlertRow {
  id: number
  pair: string
  timeframe: string
  side: 'long' | 'short'
  source: string
  zone_min: number
  zone_max: number
  entry: number
  stop: number
  target: number
  score: number
  confluence: number
  state: string
  swept_price: number | null
  size: number
  rr: number
  bias_tf?: string | null
  setup_tf?: string | null
  entry_tf?: string | null
  feed_source?: string | null
  resolved_symbol?: string | null
  price_valid?: number | boolean | null
  confirmation_state?: string | null
  last_price: number | null
  unrealized_r: number | null
  armed_at: number
  updated_at: number
  events: AlertEvent[]
}

export interface Pagination {
  page: number
  per_page: number
  total: number
  total_pages: number
}

export interface ConnectorRow {
  id: number
  name: string
  url: string
  oauth_client_id: string | null
  enabled: boolean
  has_secret: boolean
  created_at: number
  updated_at: number
}

export interface Account {
  pair: string
  starting_balance: number
  balance: number
  wins: number
  losses: number
  updated_at: number
}

export interface EquityPoint {
  ts: number
  balance: number
  r: number
}

export interface ZoneStat {
  zone_type: string
  trades: number
  win_rate: number
  avg_r: number
}

export interface PairStat {
  pair: string
  trades: number
  win_rate: number
  net_r: number
  net_cash: number
}

export interface ClosedTradeRow {
  alert_id: number
  pair: string
  timeframe: string
  zone_type: string
  confluence: number
  swept: number
  result_r: number
  closed_at: number
  side: 'long' | 'short'
  source: string
  entry: number
  stop: number
  target: number
  size: number
  rr: number
  trader_mode?: string | null
  balance: number
  cash_delta: number
  entry_to_close_points?: number | null
  entry_to_close_pips?: number | null
  stop_distance_points?: number | null
  stop_distance_pips?: number | null
  target_distance_points?: number | null
  target_distance_pips?: number | null
  armed_at?: number
  updated_at?: number
  filled_at?: number | null
  entry_time?: number | null
  exit_time?: number | null
  exit_price?: number | null
  fill_price?: number | null
  replay_mode?: 'exact' | 'estimated' | 'unavailable'
  entry_time_source?: string | null
  exit_time_source?: string | null
  estimated_replay_reason?: string | null
  data_quality_flags?: string[]
}

export interface JournalRow {
  id: number
  pair: string
  timeframe: string
  status: string
  side: string | null
  source: string | null
  reason: string
  entry: number | null
  stop: number | null
  target: number | null
  rr: number | null
  trader_mode?: string | null
  bias_tf: string | null
  setup_tf: string | null
  entry_tf: string | null
  feed_source: string | null
  resolved_symbol: string | null
  price_valid: number | boolean | null
  reference_price: number | null
  candle_price: number | null
  quality_flags: string[]
  first_seen_at?: number | null
  last_seen_at?: number | null
  repeat_count?: number
  plan_json: {
    status?: string | null
    instruction?: string | null
    checklist?: ChecklistItem[]
    notes?: string[]
  }
  created_at: number
}

export interface StatsResponse {
  stats: {
    trades: number
    win_rate: number
    avg_r: number
    net_r: number
    net_cash: number
    by_zone: ZoneStat[]
    by_pair: PairStat[]
  }
  account: Account | null
  equity: EquityPoint[]
  open: AlertRow[]
  closed: ClosedTradeRow[]
  journal: JournalRow[]
  floating_r: number
  floating_cash: number
  equity_float: number | null
}

export interface SettingsResponse {
  settings: {
    trader_mode?: string
    ai_provider?: string
    auto_apply?: number
  }
  trader_modes: Record<string, { bias_tf: string; setup_tf: string; entry_tf: string }>
  ai_providers?: Record<string, { label: string; free: boolean; configured: boolean; status: string; detail: string; model?: string }>
}

export interface TakeTradeResult {
  id: number
  pair: string
  timeframe: string
  side: 'long' | 'short'
  entry: number
  stop: number
  target: number
  size: number
  rr: number
  state: string
  monitor: MonitorStatus
}

export interface ScanResult {
  pair: string
  pair_display: string
  timeframe: string
  generated_at?: number
  price: number
  change_pct: number
  structure: MarketStructure
  candles: Candle[]
  pattern_catalog: PatternCatalogGroup[]
  pattern_catalog_flat: string[]
  chart_pattern_catalog: PatternCatalogGroup[]
  layers: {
    swings: { highs: { time: number; price: number }[]; lows: { time: number; price: number }[] }
    sr: Zone[]
    supply_demand: Zone[]
    fvg: Zone[]
    moving_averages: MASeries[]
    patterns_high: Pattern[]
    patterns_all: Pattern[]
    chart_patterns: ChartPattern[]
  }
  future: {
    bars_ahead: number
    bias: string
    entries: Entry[]
  }
  timeframe_roles?: {
    trader_mode?: string
    bias_tf: string
    setup_tf: string
    entry_tf: string
  }
  feed?: {
    pair: string
    timeframe: string
    feed_source: string
    resolved_symbol: string
    price_valid: boolean
    reference_price?: number | null
    candle_price?: number | null
    price_drift?: number | null
    feed_error?: string | null
    tick_source?: string | null
    candle_source?: string | null
    warming?: boolean
    realtime?: boolean
    provider_epoch?: number | null
    received_at?: number | null
    age?: number | null
    stale?: boolean | null
    fallback_reason?: string | null
  }
  rescan?: RescanMeta
  verdict: Verdict
}

// ---- end-of-day what-if review + strategy recommendations ----------------
export interface EodGateStat {
  gate: string
  label: string
  count: number
  filled: number
  unfilled: number
  would_have_won: number
  would_have_lost: number
  still_open: number
  unscored: number
  missed_r: number
  avoided_loss_r: number
  avg_cf_r: number
  fill_rate: number
  win_rate: number | null
  flaw: boolean
  note: string | null
}

export interface EodMissedWinner {
  gate: string
  side: string | null
  source: string | null
  entry: number | null
  stop: number | null
  target: number | null
  counterfactual_r: number
  first_seen_at: number
  reason: string | null
}

export interface Formation {
  run_before?: number
  body_frac?: number
  lower_wick_frac?: number
  upper_wick_frac?: number
  swept?: boolean
  sma_pos?: 'below' | 'above'
  atr_ratio?: number | null
  hour_eat?: number
  kind?: string
  summary?: string
}

export interface MissedMove {
  direction: 'long' | 'short'
  origin_epoch: number
  origin_price: number
  entry?: number
  stop?: number
  peak_epoch: number
  peak_price: number
  rr: number
  coverage_kind: string
  covered: boolean
  formation?: Formation
}

export interface EodReview {
  pair: string
  day: string
  day_start: number
  day_end: number
  entry_tf?: string
  realized_r: number
  taken_trades: number
  rejections_total: number
  rejections_scored: number
  missed_r: number
  avoided_loss_r: number
  gates: EodGateStat[]
  missed_winners: EodMissedWinner[]
  missed_moves: MissedMove[]
  missed_moves_total: number
  missed_moves_blind: number
  biggest_blind_r: number
  flaws: string[]
  summary: string
}

export interface EodReviewRow {
  id: number
  pair: string
  day: string
  realized_r: number | null
  missed_r: number | null
  provider: string | null
  created_at: number
  review: EodReview
  flags: string[]
}

export interface Recommendation {
  id: number
  pair: string
  eod_review_id: number | null
  param: string
  current_value: number | null
  proposed_value: number | null
  rationale: string | null
  expected_added_trades: number | null
  expected_r_impact: number | null
  shadow_delta_r: number | null
  shadow_added_trades: number | null
  confidence: string | null
  status: string
  created_at: number
  updated_at: number
}

export interface EodReviewResponse {
  pair: string
  latest: EodReviewRow | null
  history: EodReviewRow[]
  recommendations: Recommendation[]
}

export interface EodReviewRunResult {
  review_id: number
  review: EodReview
  recommendations: Recommendation[]
  provider: string
}

export interface HistoryOpportunity extends MissedMove {
  day: string
}

export interface HistoryScanResult {
  pair: string
  scan_tf: string | null
  days_scanned: number
  opportunities: HistoryOpportunity[]
  per_day: Array<{ day: string; missed_moves_blind: number; missed_r: number; realized_r: number }>
  totals: { blind: number; vetoed: number; missed_r_blind: number }
  summary: string
  recommendations?: Recommendation[]
  auto_apply?: boolean
}

export interface OverridesResponse {
  pair: string
  overrides: Record<string, number>
  applied: Recommendation[]
}
