package com.tradeguard.app.data.repo

import com.tradeguard.app.data.model.Candle
import com.tradeguard.app.data.model.Future
import com.tradeguard.app.data.model.Layers
import com.tradeguard.app.data.model.ScanResult
import com.tradeguard.app.data.model.Structure
import com.tradeguard.app.data.model.Verdict
import com.tradeguard.app.data.model.VerdictPlan
import com.tradeguard.app.data.model.Zone
import com.tradeguard.app.data.model.Pattern

/** Small on-device projection engine. It is intentionally conservative: it never claims certainty. */
object LocalAnalysisEngine {
    fun scan(pair: String, display: String, tf: String, candles: List<Candle>): ScanResult {
        val recent = candles.takeLast(40)
        val first = recent.firstOrNull()?.close ?: 0.0
        val last = recent.lastOrNull()?.close ?: 0.0
        val change = if (first == 0.0) 0.0 else (last - first) / first * 100.0
        val up = change > 0.05
        val down = change < -0.05
        val structure = Structure(
            state = when { up -> "BULLISH"; down -> "BEARISH"; else -> "RANGE" },
            confidence = (kotlin.math.abs(change) / 2.0).coerceIn(0.25, 0.75),
            reason = "Local Deriv candle sample: ${if (up) "higher closes" else if (down) "lower closes" else "mixed structure"}.",
            rangeHigh = recent.maxOfOrNull { it.high },
            rangeLow = recent.minOfOrNull { it.low },
        )
        val state = if (kotlin.math.abs(change) >= 0.35) "WAIT" else "STAND_ASIDE"
        val side = if (up) "long" else "short"
        val plan = if (last > 0.0) {
            val risk = (last * 0.002).coerceAtLeast(0.00001)
            VerdictPlan(side, last, if (up) last - risk else last + risk, if (up) last + risk * 2 else last - risk * 2,
                2.0, 0.0, 1.0, 1, structure.confidence)
        } else null
        val window = recent.takeLast(24)
        val low = window.minOfOrNull { it.low } ?: last
        val high = window.maxOfOrNull { it.high } ?: last
        val band = ((high - low) * 0.08).coerceAtLeast(last * 0.0002)
        val zones = Layers(
            sr = listOf(
                Zone(layer = "sr", time = window.firstOrNull()?.time, levelMin = low, levelMax = low + band, levelMid = low + band / 2, kind = "support", priority = "high", priorityScore = 1.0),
                Zone(layer = "sr", time = window.firstOrNull()?.time, levelMin = high - band, levelMax = high, levelMid = high - band / 2, kind = "resistance", priority = "high", priorityScore = 1.0),
            ),
            supplyDemand = listOf(
                Zone(layer = "supply_demand", time = window.firstOrNull()?.time, startTime = window.firstOrNull()?.time, endTime = window.lastOrNull()?.time, levelMin = low, levelMax = low + band * 1.5, levelMid = low + band * .75, kind = "demand", fresh = true, priority = "high", priorityScore = .9),
                Zone(layer = "supply_demand", time = window.firstOrNull()?.time, startTime = window.firstOrNull()?.time, endTime = window.lastOrNull()?.time, levelMin = high - band * 1.5, levelMax = high, levelMid = high - band * .75, kind = "supply", fresh = true, priority = "high", priorityScore = .9),
            ),
            fvg = findGaps(window),
            patternsHigh = listOfNotNull(recent.lastOrNull()?.let { Pattern(it.time, "latest", if (it.close >= it.open) "bullish" else "bearish", it.close, .4) }),
        )
        return ScanResult(
            pair = pair, pairDisplay = display, timeframe = tf, price = last, changePct = change,
            structure = structure, candles = candles, layers = zones, future = Future(bias = side),
            verdict = Verdict(
                state = state,
                headline = if (up) "Potential buy setup" else if (down) "Potential sell setup" else "No clear setup",
                reasons = listOf(structure.reason, "Projection is based on the latest local candle sample, not a confirmation."),
                plan = plan,
                enterBlockedBy = "a confirming close and fresh zone reaction",
            ),
        )
    }

    private fun findGaps(candles: List<Candle>): List<Zone> = (2 until candles.size).mapNotNull { i ->
        val left = candles[i - 2]; val right = candles[i]
        when {
            right.low > left.high -> Zone("fvg", time = left.time, startTime = left.time, levelMin = left.high, levelMax = right.low, levelMid = (left.high + right.low) / 2, kind = "bullish", priority = "high", priorityScore = .8, bottom = left.high, top = right.low)
            right.high < left.low -> Zone("fvg", time = left.time, startTime = left.time, levelMin = right.high, levelMax = left.low, levelMid = (right.high + left.low) / 2, kind = "bearish", priority = "high", priorityScore = .8, bottom = right.high, top = left.low)
            else -> null
        }
    }.takeLast(4)
}
