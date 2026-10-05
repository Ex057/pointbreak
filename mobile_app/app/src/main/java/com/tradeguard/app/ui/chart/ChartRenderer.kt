package com.tradeguard.app.ui.chart

import android.graphics.Paint
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import com.tradeguard.app.data.model.Candle
import com.tradeguard.app.data.model.Pattern
import com.tradeguard.app.data.model.Zone
import com.tradeguard.app.ui.theme.DownRed
import com.tradeguard.app.ui.theme.FvgPurple
import com.tradeguard.app.ui.theme.Gold
import com.tradeguard.app.ui.theme.UpGreen

/** Paints created once and reused, since Android text drawing needs them. */
class ChartPaints {
    val axis = Paint().apply { color = 0xFF9A9AA2.toInt(); textSize = 24f; isAntiAlias = true }
    val zoneLabel = Paint().apply { textSize = 26f; isAntiAlias = true; isFakeBoldText = true }
    val pattern = Paint().apply { color = 0xFFF5F5F7.toInt(); textSize = 22f; isAntiAlias = true }
    val priceTag = Paint().apply { color = 0xFF0B0B0D.toInt(); textSize = 24f; isAntiAlias = true; isFakeBoldText = true }
}

/**
 * Draw the chart in the current DrawScope. The inline view runs this at scale 1;
 * the fullscreen view wraps it in a scale/translate transform, so the same vector
 * art stays crisp at any zoom.
 */
fun DrawScope.drawChart(
    candles: List<Candle>,
    srZones: List<Zone>,
    sdZones: List<Zone>,
    fvgZones: List<Zone>,
    patterns: List<Pattern>,
    paints: ChartPaints,
    gutterPx: Float,
    showTrendline: Boolean = true,
) {
    if (candles.isEmpty()) return
    fun dp(v: Float): Float = v * density
    val nativeCanvas = drawContext.canvas.nativeCanvas

    val padTop = dp(10f); val padBottom = dp(10f)
    val plotW = size.width - gutterPx
    val h = size.height - padTop - padBottom

    var lo = Double.MAX_VALUE; var hi = Double.MIN_VALUE
    for (c in candles) { if (c.low < lo) lo = c.low; if (c.high > hi) hi = c.high }
    fun expand(z: Zone) {
        val zLo = z.bottom ?: z.levelMin; val zHi = z.top ?: z.levelMax
        if (zLo < lo) lo = zLo; if (zHi > hi) hi = zHi
    }
    srZones.forEach(::expand); sdZones.forEach(::expand); fvgZones.forEach(::expand)
    val span = (hi - lo).takeIf { it > 0 } ?: 1.0
    fun y(p: Double): Float = padTop + (h * (1.0 - (p - lo) / span)).toFloat()
    val firstTime = candles.first().time
    val lastTime = candles.last().time
    val timeSpan = (lastTime - firstTime).coerceAtLeast(1L)
    fun xForTime(time: Long?): Float = time?.let {
        (plotW * ((it - firstTime).toDouble() / timeSpan)).toFloat().coerceIn(0f, plotW)
    } ?: 0f

    // gridlines + right-axis price labels
    for (i in 0..4) {
        val price = hi - span * i / 4
        val yy = y(price)
        drawLine(Color(0x12FFFFFF), Offset(0f, yy), Offset(plotW, yy), strokeWidth = 1f)
        nativeCanvas.drawText(fmtPrice(price), plotW + dp(4f), yy + 4f, paints.axis)
    }

    // zone bands
    fun band(zLo: Double, zHi: Double, fill: Color, border: Color, label: String, from: Long? = null, to: Long? = null) {
        val top = y(zHi); val bottom = y(zLo)
        val height = (bottom - top).coerceAtLeast(2f)
        val left = xForTime(from)
        val right = if (to == null) plotW else xForTime(to).coerceAtLeast(left + 2f)
        drawRect(fill, Offset(left, top), Size(right - left, height))
        drawLine(border, Offset(left, top), Offset(right, top), strokeWidth = 1f)
        drawLine(border, Offset(left, bottom), Offset(right, bottom), strokeWidth = 1f)
        paints.zoneLabel.color = border.toArgb()
        nativeCanvas.drawText(label, left + dp(6f), top + dp(11f), paints.zoneLabel)
    }
    srZones.forEach {
        val support = it.kind == "support"; val c = if (support) UpGreen else DownRed
        band(it.levelMin, it.levelMax, c.copy(alpha = 0.06f), c.copy(alpha = 0.55f),
            "${if (support) "S" else "R"} ${fmtPrice(it.levelMid)}")
    }
    sdZones.forEach {
        val demand = it.kind == "demand"; val c = if (demand) UpGreen else DownRed
        band(it.levelMin, it.levelMax, c.copy(alpha = 0.09f), c.copy(alpha = 0.6f),
            (if (demand) "demand" else "supply") + if (it.fresh == true) " · fresh" else "", it.startTime ?: it.time, it.endTime)
    }
    fvgZones.forEach {
        band(it.bottom ?: it.levelMin, it.top ?: it.levelMax, FvgPurple.copy(alpha = 0.07f),
            FvgPurple.copy(alpha = 0.5f), "FVG", it.startTime ?: it.time, it.endTime)
    }

    // candles
    val n = candles.size
    val slot = plotW / n
    val bodyW = (slot * 0.62f).coerceIn(1f, 16f)
    val timeToX = HashMap<Long, Float>(n)
    candles.forEachIndexed { i, c ->
        val cx = slot * i + slot / 2f
        timeToX[c.time] = cx
        val up = c.close >= c.open
        val color = if (up) UpGreen else DownRed
        drawLine(color, Offset(cx, y(c.high)), Offset(cx, y(c.low)), strokeWidth = 1.5f)
        val openY = y(c.open); val closeY = y(c.close)
        val top = minOf(openY, closeY); val bottom = maxOf(openY, closeY)
        drawRect(color, Offset(cx - bodyW / 2f, top), Size(bodyW, (bottom - top).coerceAtLeast(1.5f)))
    }

    if (showTrendline && candles.size > 1) {
        val first = candles.first(); val last = candles.last()
        drawLine(Color(0xFFB68CFF), Offset(0f, y(first.close)), Offset(plotW, y(last.close)), strokeWidth = 2f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 7f)))
    }

    // candlestick patterns — small marker + name at the candle
    patterns.forEach { p ->
        val cx = timeToX[p.time] ?: return@forEach
        val bull = p.direction == "bullish"
        val markerColor = if (bull) UpGreen else if (p.direction == "bearish") DownRed else Gold
        val py = if (bull) y(p.price) + dp(14f) else y(p.price) - dp(14f)
        val tri = Path().apply {
            if (bull) { moveTo(cx, py - dp(7f)); lineTo(cx - dp(5f), py); lineTo(cx + dp(5f), py) }
            else { moveTo(cx, py + dp(7f)); lineTo(cx - dp(5f), py); lineTo(cx + dp(5f), py) }
            close()
        }
        drawPath(tri, markerColor)
        paints.pattern.color = markerColor.toArgb()
        nativeCanvas.drawText(p.name, cx + dp(7f), py + 4f, paints.pattern)
    }

    // last-price dashed line
    val lastY = y(candles.last().close)
    drawLine(
        Color(0x88FFFFFF), Offset(0f, lastY), Offset(plotW, lastY), strokeWidth = 1f,
        pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)),
    )
}

/**
 * Cap + prioritise patterns so a fullscreen chart shows the meaningful ones, not
 * all ~100. Prefer patterns sitting at a zone, then by strength.
 */
fun topPatterns(all: List<Pattern>, cap: Int): List<Pattern> =
    all.sortedWith(compareByDescending<Pattern> { it.atZone }.thenByDescending { it.strength })
        .take(cap)

private fun fmtPrice(v: Double): String =
    if (kotlin.math.abs(v) >= 1000) String.format("%.0f", v) else String.format("%.2f", v)
