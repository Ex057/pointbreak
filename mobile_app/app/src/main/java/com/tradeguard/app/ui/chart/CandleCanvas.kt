package com.tradeguard.app.ui.chart

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.tradeguard.app.data.model.ScanResult

// Per-layer caps: even after priority filtering, never draw more than this many
// bands of a layer. The backstop that keeps the chart from ever flooding again.
const val CAP_SR = 4
const val CAP_SD = 3
const val CAP_FVG = 4

/** The compact inline chart on the Chart tab. Static (no zoom); patterns hidden. */
@Composable
fun CandleCanvas(
    scan: ScanResult,
    layers: LayerVis,
    density: Density,
    modifier: Modifier = Modifier,
) {
    if (scan.candles.isEmpty()) return
    val paints = remember { ChartPaints() }

    val srZones = visibleZones(scan.layers.sr, layers.sr, density, CAP_SR)
    val sdZones = visibleZones(scan.layers.supplyDemand, layers.supplyDemand, density, CAP_SD)
    val fvgZones = visibleZones(scan.layers.fvg, layers.fvg, density, CAP_FVG)

    Canvas(modifier = modifier.fillMaxWidth().height(300.dp)) {
        val gutter = 46.dp.toPx()
        drawChart(scan.candles, srZones, sdZones, fvgZones, topPatterns(scan.layers.patternsHigh, 8), paints, gutter, layers.trendlines)
    }
}
