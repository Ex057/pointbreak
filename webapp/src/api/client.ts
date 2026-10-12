import axios from 'axios'
import type {
  AlertRow,
  AiAnalysisResponse,
  ConnectorRow,
  EodReviewResponse,
  EodReviewRunResult,
  HistoryScanResult,
  HealthResponse,
  OverridesResponse,
  MonitorStatus,
  Pagination,
  PriceTick,
  QuotesResponse,
  ReconnaissanceResponse,
  Recommendation,
  ResearchDataStatus,
  ResearchDataJob,
  ResearchHistory,
  PriceForecastRun,
  ResearchRun,
  ScanResult,
  SettingsResponse,
  StatsResponse,
  TakeTradeResult,
} from './types'

// Vite proxies /api -> http://127.0.0.1:8000 (see vite.config.ts), so the
// browser makes same-origin requests in dev.
const api = axios.create({ baseURL: '/api' })

export interface RequestOptions {
  signal?: AbortSignal
}

export async function fetchVerdict(
  pair: string,
  tf: string,
  minRr: number,
  traderMode?: string,
  recheckReason?: string,
): Promise<ScanResult> {
  const { data } = await api.get<ScanResult>('/verdict', {
    params: { pair, tf, min_rr: minRr, trader_mode: traderMode || undefined, recheck_reason: recheckReason || undefined },
  })
  return data
}

export async function fetchHealth() {
  const { data } = await api.get<HealthResponse>('/health')
  return data
}

export async function fetchPrice(pair: string, tf?: string) {
  const { data } = await api.get<PriceTick>('/price', { params: { pair, tf } })
  return data
}

export async function fetchQuotes(tf?: string) {
  const { data } = await api.get<QuotesResponse>('/quotes', { params: { tf } })
  return data
}

export async function fetchReconnaissance(pair: string, forceRefresh = false, options?: RequestOptions) {
  const { data } = await api.get<ReconnaissanceResponse>('/reconnaissance', {
    params: { pair, force_refresh: forceRefresh || undefined },
    signal: options?.signal,
  })
  return data
}

export async function fetchMonitor(options?: RequestOptions) {
  const { data } = await api.get<MonitorStatus>('/monitor', { signal: options?.signal })
  return data
}

export async function setMonitor(pair: string, tf: string, traderMode?: string) {
  const { data } = await api.put<MonitorStatus>('/monitor', null, {
    params: { pair, tf, trader_mode: traderMode || undefined },
  })
  return data
}

export async function fetchSettings() {
  const { data } = await api.get<SettingsResponse>('/settings')
  return data
}

export async function fetchAiAnalysis(pair: string, tf: string, traderMode?: string, forceRefresh?: boolean) {
  const { data } = await api.get<AiAnalysisResponse>('/ai-analysis', {
    params: { pair, tf, trader_mode: traderMode || undefined, force_refresh: forceRefresh || undefined },
  })
  return data
}

export async function fetchResearchDataStatus(pair = 'xauusd') {
  const { data } = await api.get<ResearchDataStatus>('/research/data-status', { params: { pair } })
  return data
}

export async function fetchResearchHistory(pair = 'xauusd') {
  const { data } = await api.get<ResearchHistory>('/research/history', { params: { pair } })
  return data
}

export async function startResearchBackfill(pair = 'xauusd', years = 2) {
  const { data } = await api.post<ResearchDataJob>('/research/history/backfill', { pair, years })
  return data
}

export async function fetchResearchBackfill(jobId: number) {
  const { data } = await api.get<ResearchDataJob>(`/research/history/backfill/${jobId}`)
  return data
}

export async function fetchPriceForecastRuns(pair = 'xauusd') {
  const { data } = await api.get<{ pair: string; runs: PriceForecastRun[] }>('/research/price-forecast/runs', { params: { pair } })
  return data.runs
}

export async function fetchPriceForecastRun(runId: number) {
  const { data } = await api.get<PriceForecastRun>(`/research/price-forecast/runs/${runId}`)
  return data
}

export async function startPriceForecast(pair = 'xauusd') {
  const { data } = await api.post<PriceForecastRun>('/research/price-forecast/runs', { pair })
  return data
}

export async function fetchResearchRuns(pair = 'xauusd') {
  const { data } = await api.get<{ pair: string; runs: ResearchRun[] }>('/research/runs', { params: { pair } })
  return data.runs
}

export async function fetchResearchRun(runId: number) {
  const { data } = await api.get<ResearchRun>(`/research/runs/${runId}`)
  return data
}

export async function startResearchRun(pair = 'xauusd', modes?: string[]) {
  const { data } = await api.post<ResearchRun>('/research/runs', { pair, modes })
  return data
}

export async function fetchEodReview(pair: string) {
  const { data } = await api.get<EodReviewResponse>('/eod-review', { params: { pair } })
  return data
}

export async function runEodReview(pair: string) {
  const { data } = await api.post<EodReviewRunResult>('/eod-review', null, { params: { pair } })
  return data
}

export async function scanHistory(pair: string, days = 60) {
  const { data } = await api.post<HistoryScanResult>('/eod-review/scan-history', null, {
    params: { pair, days },
  })
  return data
}

export async function applyRecommendation(recId: number) {
  const { data } = await api.post<{ applied: boolean; recommendation: Recommendation }>(
    `/recommendations/${recId}/apply`,
  )
  return data
}

export async function fetchOverrides(pair: string) {
  const { data } = await api.get<OverridesResponse>('/overrides', { params: { pair } })
  return data
}

export async function resetOverrides(pair: string) {
  const { data } = await api.post<{ reset: boolean; cleared: number; overrides: Record<string, number> }>(
    '/overrides/reset',
    null,
    { params: { pair } },
  )
  return data
}

export async function dismissRecommendation(recId: number) {
  const { data } = await api.post<{ dismissed: boolean; recommendation: Recommendation }>(
    `/recommendations/${recId}/dismiss`,
  )
  return data
}

export async function updateSettings(body: { trader_mode?: string; ai_provider?: string; auto_apply?: boolean }) {
  const { data } = await api.put<SettingsResponse>('/settings', body)
  return data
}

export async function fetchAlerts(pair: string, page = 1, perPage = 10, options?: RequestOptions) {
  const { data } = await api.get<{ alerts: AlertRow[]; pagination: Pagination }>('/alerts', {
    params: { pair, page, per_page: perPage },
    signal: options?.signal,
  })
  return data
}

export async function fetchStats(pair: string, options?: RequestOptions) {
  const { data } = await api.get<StatsResponse>('/stats', { params: { pair }, signal: options?.signal })
  return data
}

export async function fetchStatsRange(pair: string, dateFrom?: string, dateTo?: string, options?: RequestOptions) {
  const { data } = await api.get<StatsResponse>('/stats', {
    params: { pair, date_from: dateFrom || undefined, date_to: dateTo || undefined },
    signal: options?.signal,
  })
  return data
}

export interface TakeTradeBody {
  pair: string
  tf: string
  side: 'long' | 'short'
  stop: number
  target: number
}

export async function takeTrade(body: TakeTradeBody) {
  const { data } = await api.post<TakeTradeResult>('/trades', body)
  return data
}

export async function closeTrade(alertId: number) {
  const { data } = await api.post(`/trades/${alertId}/close`)
  return data
}

export interface CreateConnectorBody {
  name: string
  url: string
  oauth_client_id?: string
  oauth_client_secret?: string
}

export async function fetchConnectors() {
  const { data } = await api.get<{ connectors: ConnectorRow[] }>('/connectors')
  return data.connectors
}

export async function createConnector(body: CreateConnectorBody) {
  const { data } = await api.post<ConnectorRow>('/connectors', body)
  return data
}

export async function deleteConnector(connectorId: number) {
  await api.delete(`/connectors/${connectorId}`)
}
