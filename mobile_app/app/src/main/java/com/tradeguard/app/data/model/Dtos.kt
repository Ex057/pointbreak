package com.tradeguard.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Mirrors the FastAPI /verdict payload one-to-one. @SerialName is used where the
// JSON key isn't a valid/idiomatic Kotlin name. Unknown keys are ignored by the
// Json config in ApiModule, so backend additions won't break the client.

@Serializable
data class HealthResponse(
    val status: String,
    @SerialName("data_source") val dataSource: String = "",
    val timeframes: List<String> = emptyList(),
    @SerialName("default_pair") val defaultPair: String = "",
    val pairs: List<PairInfo> = emptyList(),
)

@Serializable
data class PairInfo(
    val id: String,
    val display: String,
    val code: String = "",
    val category: String = "",
    val live: Boolean = true,
)

@Serializable
data class DerivSymbol(
    @SerialName("underlying_symbol") val underlyingSymbol: String = "",
    @SerialName("underlying_symbol_name") val underlyingSymbolName: String = "",
    val symbol: String = "",
    val displayName: String = "",
)

@Serializable
data class DerivTick(
    val symbol: String = "",
    val quote: Double = 0.0,
    val epoch: Long = 0L,
)

@Serializable
data class Candle(
    val time: Long,
    val open: Double,
    val high: Double,
    val low: Double,
    val close: Double,
)

@Serializable
data class Zone(
    val layer: String = "",
    val time: Long? = null,
    @SerialName("start_time") val startTime: Long? = null,
    @SerialName("end_time") val endTime: Long? = null,
    @SerialName("level_min") val levelMin: Double = 0.0,
    @SerialName("level_max") val levelMax: Double = 0.0,
    @SerialName("level_mid") val levelMid: Double = 0.0,
    val kind: String = "",
    val touches: Int? = null,
    val fresh: Boolean? = null,
    val filled: Boolean? = null,
    val top: Double? = null,
    val bottom: Double? = null,
    val priority: String? = null,
    @SerialName("priority_score") val priorityScore: Double = 0.0,
)

@Serializable
data class Entry(
    val side: String,
    val source: String,
    @SerialName("entry_price") val entryPrice: Double,
    val stop: Double,
    val target: Double,
    @SerialName("eta_bars") val etaBars: Int,
    @SerialName("t_future") val tFuture: Long,
    val confluence: Int,
    val score: Double,
)

@Serializable
data class Structure(
    val state: String,
    val confidence: Double,
    val reason: String,
    @SerialName("range_high") val rangeHigh: Double? = null,
    @SerialName("range_low") val rangeLow: Double? = null,
)

@Serializable
data class VerdictPlan(
    val side: String,
    val entry: Double,
    val stop: Double,
    val target: Double,
    val rr: Double,
    val size: Double,
    @SerialName("risk_pct") val riskPct: Double,
    val confluence: Int,
    val score: Double,
)

@Serializable
data class NewsEvent(
    val title: String,
    val currency: String = "",
    val impact: String = "",
    @SerialName("minutes_away") val minutesAway: Int = 0,
)

@Serializable
data class Verdict(
    val state: String,
    val headline: String,
    val reasons: List<String> = emptyList(),
    val plan: VerdictPlan? = null,
    val news: NewsEvent? = null,
    @SerialName("enter_blocked_by") val enterBlockedBy: String? = null,
)

@Serializable
data class Pattern(
    val time: Long,
    val name: String,
    val direction: String = "",
    val price: Double = 0.0,
    val strength: Double = 0.0,
    @SerialName("at_zone") val atZone: Boolean = false,
    val span: Int = 1,
)

@Serializable
data class Layers(
    val sr: List<Zone> = emptyList(),
    @SerialName("supply_demand") val supplyDemand: List<Zone> = emptyList(),
    val fvg: List<Zone> = emptyList(),
    @SerialName("patterns_high") val patternsHigh: List<Pattern> = emptyList(),
    @SerialName("patterns_all") val patternsAll: List<Pattern> = emptyList(),
)

@Serializable
data class Future(
    val bias: String = "",
    val entries: List<Entry> = emptyList(),
)

// ---- monitor / alerts / stats ----

@Serializable
data class AlertEvent(
    val state: String,
    val ts: Long,
    val price: Double,
    val reason: String,
)

@Serializable
data class AlertDto(
    val id: Int,
    val pair: String = "",
    val timeframe: String = "",
    val side: String = "",
    val source: String = "",
    @SerialName("zone_min") val zoneMin: Double = 0.0,
    @SerialName("zone_max") val zoneMax: Double = 0.0,
    val entry: Double = 0.0,
    val stop: Double = 0.0,
    val target: Double = 0.0,
    val score: Double = 0.0,
    val size: Double = 0.0,
    val state: String = "",
    @SerialName("last_price") val lastPrice: Double? = null,
    @SerialName("unrealized_r") val unrealizedR: Double? = null,
    val events: List<AlertEvent> = emptyList(),
)

@Serializable
data class AlertsResponse(val alerts: List<AlertDto> = emptyList())

@Serializable
data class ZoneStat(
    @SerialName("zone_type") val zoneType: String,
    val trades: Int,
    @SerialName("win_rate") val winRate: Double,
    @SerialName("avg_r") val avgR: Double,
)

@Serializable
data class StatsBlock(
    val trades: Int = 0,
    @SerialName("win_rate") val winRate: Double = 0.0,
    @SerialName("avg_r") val avgR: Double = 0.0,
    @SerialName("by_zone") val byZone: List<ZoneStat> = emptyList(),
)

@Serializable
data class Account(
    val pair: String = "",
    @SerialName("starting_balance") val startingBalance: Double = 0.0,
    val balance: Double = 0.0,
    val wins: Int = 0,
    val losses: Int = 0,
)

@Serializable
data class EquityPoint(val ts: Long, val balance: Double, val r: Double)

@Serializable
data class StatsResponse(
    val stats: StatsBlock = StatsBlock(),
    val account: Account? = null,
    val equity: List<EquityPoint> = emptyList(),
    val open: List<AlertDto> = emptyList(),
    @SerialName("floating_r") val floatingR: Double = 0.0,
    @SerialName("floating_cash") val floatingCash: Double = 0.0,
    @SerialName("equity_float") val equityFloat: Double? = null,
)

@Serializable
data class MonitorStatus(
    val pair: String? = null,
    @SerialName("pair_display") val pairDisplay: String? = null,
    val timeframe: String = "",
    val running: Boolean = false,
    @SerialName("active_alerts") val activeAlerts: Int = 0,
)

@Serializable
data class ScanResult(
    val pair: String,
    @SerialName("pair_display") val pairDisplay: String = "",
    val timeframe: String,
    val price: Double,
    @SerialName("change_pct") val changePct: Double,
    val structure: Structure? = null,
    val candles: List<Candle> = emptyList(),
    val layers: Layers = Layers(),
    val future: Future = Future(),
    val verdict: Verdict? = null,
)
