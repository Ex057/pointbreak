package com.tradeguard.app.ui.chart

import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.tradeguard.app.data.model.ScanResult
import com.tradeguard.app.ui.theme.Bg
import com.tradeguard.app.ui.theme.CardBorder
import com.tradeguard.app.ui.theme.DownRed
import com.tradeguard.app.ui.theme.FvgPurple
import com.tradeguard.app.ui.theme.Gold
import com.tradeguard.app.ui.theme.Lime
import com.tradeguard.app.ui.theme.LimeInk
import com.tradeguard.app.ui.theme.Surface1
import com.tradeguard.app.ui.theme.TextHi
import com.tradeguard.app.ui.theme.TextMid
import com.tradeguard.app.ui.theme.UpGreen
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Fullscreen, TradingView-style chart. Unlike the inline chart it uses a
 * WINDOWED model: pinch zooms the time window, drag pans through history, and
 * the price axis is FIXED on the right and auto-fits the visible candles — so
 * prices stay readable at any zoom. All layer toggles are available here, not
 * just patterns. Survives rotation (the Activity handles config changes and the
 * window state is saved).
 */
@Composable
fun FullscreenChart(
    scan: ScanResult,
    layers: LayerVis,
    density: Density,
    onToggleLayer: (ChartViewModel.Layer) -> Unit,
    onDensity: (Density) -> Unit,
    onClose: () -> Unit,
) {
    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(
            usePlatformDefaultWidth = false,
            dismissOnClickOutside = false,   // pans must never dismiss it
        ),
    ) {
        val candles = scan.candles
        val total = candles.size
        var barsVisible by rememberSaveable { mutableIntStateOf(minOf(160, total).coerceAtLeast(20)) }
        var endIndex by rememberSaveable { mutableIntStateOf(total) }   // exclusive rightmost
        var showPatterns by rememberSaveable { mutableStateOf(true) }
        var railTab by rememberSaveable { mutableStateOf("verdict") }
        val paints = remember { ChartPaints() }

        val srZones = visibleZones(scan.layers.sr, layers.sr, density, CAP_SR)
        val sdZones = visibleZones(scan.layers.supplyDemand, layers.supplyDemand, density, CAP_SD)
        val fvgZones = visibleZones(scan.layers.fvg, layers.fvg, density, CAP_FVG)
        val patterns = if (showPatterns) topPatterns(scan.layers.patternsHigh, cap = 20) else emptyList()

        Box(modifier = Modifier.fillMaxSize().background(Bg)) {
            Row(modifier = Modifier.fillMaxSize().padding(top = 78.dp, bottom = 18.dp)) {
                Box(modifier = Modifier.weight(0.76f).fillMaxHeight()) {
                    Canvas(
                        modifier = Modifier
                            .fillMaxSize()
                    .pointerInput(total) {
                        detectTransformGestures { _, pan, zoom, _ ->
                            val plotW = size.width - 64.dp.toPx()
                            if (zoom != 1f) {
                                barsVisible = (barsVisible / zoom).roundToInt().coerceIn(20, total)
                            }
                            val barW = plotW / barsVisible
                            endIndex = (endIndex - (pan.x / barW)).roundToInt()
                                .coerceIn(barsVisible, total)
                        }
                    }
                    .pointerInput(Unit) {
                        detectTapGestures(onDoubleTap = {
                            barsVisible = minOf(160, total).coerceAtLeast(20)
                            endIndex = total
                        })
                    },
                    ) {
                        drawWindowedChart(
                            scan = scan,
                            startIndex = (endIndex - barsVisible).coerceAtLeast(0),
                            endIndex = endIndex,
                            srZones = srZones, sdZones = sdZones, fvgZones = fvgZones,
                            patterns = patterns, paints = paints, showTrendline = layers.trendlines,
                        )
                    }
                }
                AnalysisRail(
                    scan = scan,
                    tab = railTab,
                    onTab = { railTab = it },
                    modifier = Modifier.weight(0.24f).fillMaxHeight(),
                )
            }

            // top overlay: title + controls
            Row(
                modifier = Modifier.fillMaxWidth().padding(start = 14.dp, top = 12.dp, end = 60.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "${scan.pairDisplay}  ·  ${scan.timeframe}",
                    color = TextHi, fontWeight = FontWeight.Bold, fontSize = 15.sp,
                )
                scan.structure?.let { structure ->
                    Text(
                        "  ${structure.state} ${(structure.confidence * 100).roundToInt()}%",
                        color = if (structure.state == "BULLISH") UpGreen else if (structure.state == "BEARISH") DownRed else Gold,
                        fontSize = 12.sp, fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(start = 12.dp),
                    )
                }
            }

            // layer + density + patterns controls (scrollable, below the title)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 44.dp, start = 14.dp, end = 14.dp)
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Chip("S/R", layers.sr) { onToggleLayer(ChartViewModel.Layer.SR) }
                Chip("D/S", layers.supplyDemand) { onToggleLayer(ChartViewModel.Layer.SUPPLY_DEMAND) }
                Chip("FVG", layers.fvg) { onToggleLayer(ChartViewModel.Layer.FVG) }
                Chip("Trend", layers.trendlines) { onToggleLayer(ChartViewModel.Layer.TRENDLINES) }
                Chip("Patterns", showPatterns) { showPatterns = !showPatterns }
                Seg("Key", density == Density.KEY) { onDensity(Density.KEY) }
                Seg("More", density == Density.MORE) { onDensity(Density.MORE) }
                Seg("Low", density == Density.LOW) { onDensity(Density.LOW) }
                Seg("All", density == Density.ALL) { onDensity(Density.ALL) }
                Seg("80", barsVisible == minOf(80, total)) { barsVisible = minOf(80, total).coerceAtLeast(20) }
                Seg("160", barsVisible == minOf(160, total)) { barsVisible = minOf(160, total).coerceAtLeast(20) }
                Seg("240", barsVisible == minOf(240, total)) { barsVisible = minOf(240, total).coerceAtLeast(20) }
            }

            Text(
                "pinch to zoom · drag to pan · double-tap to reset",
                color = TextMid, fontSize = 11.sp,
                modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 6.dp),
            )
            Icon(
                Icons.Filled.Close, contentDescription = "Close", tint = TextHi,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(top = 10.dp, end = 12.dp)
                    .clip(RoundedCornerShape(20.dp))
                    .background(Surface1)
                    .border(1.dp, CardBorder, RoundedCornerShape(20.dp))
                    .clickable(onClick = onClose)
                    .padding(7.dp),
            )
        }
    }
}

@Composable
private fun AnalysisRail(
    scan: ScanResult,
    tab: String,
    onTab: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .padding(start = 10.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Surface1)
            .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
            .padding(10.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(5.dp), modifier = Modifier.fillMaxWidth()) {
            listOf("verdict" to "Verdict", "patterns" to "Patterns", "structure" to "Structure", "context" to "Context").forEach { (key, label) ->
                Text(label, color = if (tab == key) Lime else TextMid, fontSize = 10.sp,
                    fontWeight = if (tab == key) FontWeight.Bold else FontWeight.Normal,
                    modifier = Modifier.clickable { onTab(key) }.padding(vertical = 6.dp))
            }
        }
        when (tab) {
            "verdict" -> {
                val verdict = scan.verdict
                Text(verdict?.state ?: "WAIT", color = when (verdict?.state) {
                    "ENTER" -> UpGreen
                    "STAND_ASIDE" -> DownRed
                    else -> Gold
                }, fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.padding(top = 12.dp))
                Text(verdict?.headline ?: "Analysis pending", color = TextHi, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 4.dp))
                verdict?.reasons?.forEach { Text("• $it", color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp)) }
                Text("Projection, not confirmation", color = Gold, fontSize = 11.sp, modifier = Modifier.padding(top = 14.dp))
                verdict?.enterBlockedBy?.let { Text("Waiting for: $it", color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 5.dp)) }
            }
            "patterns" -> {
                Text("Detected patterns", color = TextHi, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 12.dp))
                if (scan.layers.patternsHigh.isEmpty()) Text("No qualified patterns in this window.", color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp))
                scan.layers.patternsHigh.forEach { p ->
                    Text("${p.name} · ${p.direction}", color = if (p.direction == "bullish") UpGreen else DownRed, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp))
                    Text("${p.price} · strength ${String.format("%.0f", p.strength * 100)}%", color = TextMid, fontSize = 10.sp)
                }
            }
            "structure" -> {
                val structure = scan.structure
                Text("Market structure", color = TextHi, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 12.dp))
                Text(structure?.state ?: "Unknown", color = when (structure?.state) { "BULLISH" -> UpGreen; "BEARISH" -> DownRed; else -> Gold }, fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.padding(top = 8.dp))
                Text(structure?.reason ?: "No structure explanation available.", color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 6.dp))
                structure?.rangeHigh?.let { Text("Range high  ${formatRailPrice(it)}", color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 10.dp)) }
                structure?.rangeLow?.let { Text("Range low   ${formatRailPrice(it)}", color = TextMid, fontSize = 11.sp) }
            }
            else -> {
                Text("Chart context", color = TextHi, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 12.dp))
                Text("${scan.pairDisplay} · ${scan.timeframe}", color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 8.dp))
                scan.candles.lastOrNull()?.let { candle ->
                    Text("O ${formatRailPrice(candle.open)}", color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 10.dp))
                    Text("H ${formatRailPrice(candle.high)}", color = TextMid, fontSize = 11.sp)
                    Text("L ${formatRailPrice(candle.low)}", color = TextMid, fontSize = 11.sp)
                    Text("C ${formatRailPrice(candle.close)}", color = TextHi, fontWeight = FontWeight.Bold, fontSize = 11.sp)
                }
                Text("Layers are anchored to their formation candles.", color = TextMid, fontSize = 10.sp, modifier = Modifier.padding(top = 14.dp))
            }
        }
    }
}

private fun formatRailPrice(v: Double): String = if (kotlin.math.abs(v) >= 1000) String.format("%.2f", v) else String.format("%.5f", v)

@Composable
private fun Chip(label: String, on: Boolean, onClick: () -> Unit) {
    Text(
        label,
        color = if (on) Lime else TextMid, fontSize = 12.sp,
        fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
        modifier = Modifier
            .clip(RoundedCornerShape(20.dp))
            .background(if (on) Lime.copy(alpha = 0.12f) else Surface1)
            .border(1.dp, if (on) Lime.copy(alpha = 0.6f) else CardBorder, RoundedCornerShape(20.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

@Composable
private fun Seg(label: String, on: Boolean, onClick: () -> Unit) {
    Text(
        label,
        color = if (on) LimeInk else TextMid, fontSize = 12.sp,
        fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
        modifier = Modifier
            .clip(RoundedCornerShape(7.dp))
            .background(if (on) Lime else Surface1)
            .border(1.dp, CardBorder, RoundedCornerShape(7.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

/** Windowed renderer: fixed axes, auto-fit price, only the visible candle slice. */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawWindowedChart(
    scan: ScanResult,
    startIndex: Int,
    endIndex: Int,
    srZones: List<com.tradeguard.app.data.model.Zone>,
    sdZones: List<com.tradeguard.app.data.model.Zone>,
    fvgZones: List<com.tradeguard.app.data.model.Zone>,
    patterns: List<com.tradeguard.app.data.model.Pattern>,
    paints: ChartPaints,
    showTrendline: Boolean,
) {
    val all = scan.candles
    if (all.isEmpty()) return
    fun dp(v: Float) = v * density
    val nc = drawContext.canvas.nativeCanvas

    val start = startIndex.coerceIn(0, all.lastIndex)
    val end = endIndex.coerceIn(start + 1, all.size)
    val visible = all.subList(start, end)

    val gutter = dp(64f)           // fixed right price axis
    val timeAxisH = dp(26f)        // fixed bottom time axis
    val topPad = dp(90f)           // clear the controls overlay
    val plotW = size.width - gutter
    val plotBottom = size.height - timeAxisH
    val plotH = plotBottom - topPad

    // auto-fit price to visible candles (TradingView default)
    var lo = Double.MAX_VALUE; var hi = Double.MIN_VALUE
    for (c in visible) { if (c.low < lo) lo = c.low; if (c.high > hi) hi = c.high }
    val pad = (hi - lo) * 0.08
    lo -= pad; hi += pad
    val span = (hi - lo).takeIf { it > 0 } ?: 1.0
    fun y(p: Double): Float = topPad + (plotH * (1.0 - (p - lo) / span)).toFloat()
    val firstTime = visible.first().time
    val lastTime = visible.last().time
    val timeSpan = (lastTime - firstTime).coerceAtLeast(1L)
    fun xForTime(time: Long?): Float = time?.let {
        (plotW * ((it - firstTime).toDouble() / timeSpan)).toFloat().coerceIn(0f, plotW)
    } ?: 0f

    val barsVisible = end - start
    val barW = plotW / barsVisible
    fun x(i: Int): Float = (i - start) * barW + barW / 2f

    // right price axis: gridlines + labels (fixed)
    for (i in 0..5) {
        val price = hi - span * i / 5
        val yy = y(price)
        drawLine(Color(0x12FFFFFF), Offset(0f, yy), Offset(plotW, yy), strokeWidth = 1f)
        nc.drawText(fmt(price), plotW + dp(5f), yy + 4f, paints.axis)
    }

    // zone bands — only those overlapping the visible price range
    fun band(zLo: Double, zHi: Double, fill: Color, border: Color, label: String, from: Long? = null, to: Long? = null) {
        if (zHi < lo || zLo > hi) return
        val top = y(minOf(zHi, hi)); val bottom = y(maxOf(zLo, lo))
        val left = xForTime(from); val right = if (to == null) plotW else xForTime(to).coerceAtLeast(left + 2f)
        drawRect(fill, Offset(left, top), Size(right - left, (bottom - top).coerceAtLeast(2f)))
        drawLine(border, Offset(left, top), Offset(right, top), strokeWidth = 1f)
        drawLine(border, Offset(left, bottom), Offset(right, bottom), strokeWidth = 1f)
        paints.zoneLabel.color = border.toArgb()
        nc.drawText(label, left + dp(6f), top + dp(11f), paints.zoneLabel)
    }
    srZones.forEach {
        val s = it.kind == "support"; val c = if (s) UpGreen else DownRed
        band(it.levelMin, it.levelMax, c.copy(alpha = 0.06f), c.copy(alpha = 0.55f),
            "${if (s) "S" else "R"} ${fmt(it.levelMid)}")
    }
    sdZones.forEach {
        val d = it.kind == "demand"; val c = if (d) UpGreen else DownRed
        band(it.levelMin, it.levelMax, c.copy(alpha = 0.09f), c.copy(alpha = 0.6f),
            (if (d) "demand" else "supply") + if (it.fresh == true) " · fresh" else "", it.startTime ?: it.time, it.endTime)
    }
    fvgZones.forEach {
        band(it.bottom ?: it.levelMin, it.top ?: it.levelMax,
            FvgPurple.copy(alpha = 0.07f), FvgPurple.copy(alpha = 0.5f), "FVG", it.startTime ?: it.time, it.endTime)
    }

    // candles (visible slice)
    val bodyW = (barW * 0.62f).coerceIn(1f, 26f)
    val timeToX = HashMap<Long, Float>(visible.size)
    for (i in start until end) {
        val c = all[i]
        val cx = x(i); timeToX[c.time] = cx
        val up = c.close >= c.open
        val color = if (up) UpGreen else DownRed
        drawLine(color, Offset(cx, y(c.high)), Offset(cx, y(c.low)), strokeWidth = 1.5f)
        val oY = y(c.open); val cY = y(c.close)
        drawRect(color, Offset(cx - bodyW / 2f, minOf(oY, cY)),
            Size(bodyW, (kotlin.math.abs(cY - oY)).coerceAtLeast(1.5f)))
    }

    if (showTrendline && visible.size > 1) {
        drawLine(Color(0xFFB68CFF), Offset(0f, y(visible.first().close)), Offset(plotW, y(visible.last().close)),
            strokeWidth = dp(2f), pathEffect = PathEffect.dashPathEffect(floatArrayOf(dp(10f), dp(7f))))
    }

    // patterns in view
    patterns.forEach { p ->
        val cx = timeToX[p.time] ?: return@forEach
        val bull = p.direction == "bullish"
        val col = if (bull) UpGreen else if (p.direction == "bearish") DownRed else Gold
        val py = if (bull) y(p.price) + dp(14f) else y(p.price) - dp(14f)
        val tri = Path().apply {
            if (bull) { moveTo(cx, py - dp(7f)); lineTo(cx - dp(5f), py); lineTo(cx + dp(5f), py) }
            else { moveTo(cx, py + dp(7f)); lineTo(cx - dp(5f), py); lineTo(cx + dp(5f), py) }
            close()
        }
        drawPath(tri, col)
        paints.pattern.color = col.toArgb()
        nc.drawText(p.name, cx + dp(7f), py + 4f, paints.pattern)
    }

    // last price line + tag on the axis
    val lastC = all[end - 1]
    val lastY = y(lastC.close)
    drawLine(Color(0x88FFFFFF), Offset(0f, lastY), Offset(plotW, lastY), strokeWidth = 1f,
        pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)))
    drawRect(Lime, Offset(plotW, lastY - dp(9f)), Size(gutter, dp(18f)))
    paints.priceTag.color = LimeInk.toArgb()
    nc.drawText(fmt(lastC.close), plotW + dp(5f), lastY + 4f, paints.priceTag)

    // bottom time axis: a few labels
    val fmtT = timeFormat(scan.timeframe)
    val labels = 4
    for (k in 0..labels) {
        val idx = start + (barsVisible - 1) * k / labels
        if (idx in start until end) {
            val cx = x(idx)
            val t = fmtT.format(Date(all[idx].time * 1000L))
            nc.drawText(t, cx - dp(18f), plotBottom + dp(17f), paints.axis)
        }
    }
}

private fun fmt(v: Double): String =
    if (kotlin.math.abs(v) >= 1000) String.format("%.1f", v) else String.format("%.2f", v)

private fun timeFormat(tf: String): SimpleDateFormat =
    if (tf.contains("D") || tf.contains("W")) SimpleDateFormat("d MMM", Locale.US)
    else SimpleDateFormat("d/M HH:mm", Locale.US)
