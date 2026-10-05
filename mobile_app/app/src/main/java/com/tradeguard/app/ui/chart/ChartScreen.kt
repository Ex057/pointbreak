package com.tradeguard.app.ui.chart

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.platform.LocalContext
import android.app.Activity
import android.content.pm.ActivityInfo
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.tradeguard.app.data.model.Structure
import com.tradeguard.app.ui.theme.AmberBg
import com.tradeguard.app.ui.theme.AmberInk
import com.tradeguard.app.ui.theme.CardBorder
import com.tradeguard.app.ui.theme.DownRed
import com.tradeguard.app.ui.theme.GreenBg
import com.tradeguard.app.ui.theme.GreenInk
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.Lime
import com.tradeguard.app.ui.theme.LimeInk
import com.tradeguard.app.ui.theme.RedBg
import com.tradeguard.app.ui.theme.RedInk
import com.tradeguard.app.ui.theme.Surface1
import com.tradeguard.app.ui.theme.Surface2
import com.tradeguard.app.ui.theme.TextMid
import com.tradeguard.app.ui.theme.UpGreen

@Composable
fun ChartScreen(vm: ChartViewModel = viewModel()) {
    val s by vm.state.collectAsStateWithLifecycle()
    var fullscreen by rememberSaveable { mutableStateOf(false) }
    val activity = LocalContext.current as? Activity
    LaunchedEffect(fullscreen) {
        activity?.requestedOrientation = if (fullscreen) ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        else ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
    }

    if (fullscreen && s.scan != null) {
        FullscreenChart(
            scan = s.scan!!,
            layers = s.layers,
            density = s.density,
            onToggleLayer = vm::toggleLayer,
            onDensity = vm::setDensity,
            onClose = { fullscreen = false },
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        // --- header: pair + price ---
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Text(
                s.scan?.pairDisplay ?: displayFor(s, s.selectedPair),
                fontWeight = FontWeight.Bold, fontSize = 20.sp, color = Ink,
            )
            Row(modifier = Modifier.weight(1f), horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.Bottom) {
                s.scan?.let { scan ->
                    Text(String.format("%.5f", s.liveQuote?.quote ?: scan.price), fontWeight = FontWeight.Bold,
                        fontSize = 20.sp, color = Ink)
                    Text(
                        (if (scan.changePct >= 0) "  +" else "  ") + String.format("%.2f%%", scan.changePct),
                        color = if (scan.changePct >= 0) UpGreen else DownRed,
                        fontSize = 14.sp, modifier = Modifier.padding(bottom = 2.dp),
                    )
                }
            }
        }

        s.scan?.candles?.lastOrNull()?.let { candle ->
            Text("Open ${fmt(candle.open)}   High ${fmt(candle.high)}   Low ${fmt(candle.low)}   Close ${fmt(candle.close)}",
                color = TextMid, fontSize = 11.sp, modifier = Modifier.padding(top = 5.dp))
        }

        // --- timeframe pills ---
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 12.dp)
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            s.timeframes.forEach { tf ->
                val on = tf == s.selectedTf
                Text(
                    tf,
                    color = if (on) LimeInk else TextMid,
                    fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(if (on) Lime else Surface1)
                        .border(1.dp, if (on) Lime else CardBorder, RoundedCornerShape(8.dp))
                        .clickable { vm.selectTimeframe(tf) }
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                )
            }
        }

        Text("Choose another market from Markets", color = TextMid, fontSize = 12.sp,
            modifier = Modifier.padding(top = 10.dp))

        // --- structure badge ---
        s.scan?.structure?.let { StructureRow(it) }

        // --- body: loading / error / chart+verdict ---
        when {
            s.loading && s.scan == null -> Box_Loading()
            s.error != null && s.scan == null -> ErrorBox(s.error!!) { vm.load() }
            s.scan != null -> {
                LayerControls(
                    layers = s.layers,
                    density = s.density,
                    onToggle = vm::toggleLayer,
                    onDensity = vm::setDensity,
                )
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 10.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .border(1.dp, CardBorder, RoundedCornerShape(10.dp)),
                ) {
                    CandleCanvas(s.scan!!, layers = s.layers, density = s.density)
                    // fullscreen affordance — bottom-right of the chart
                    Icon(
                        Icons.Filled.Fullscreen,
                        contentDescription = "Open fullscreen chart",
                        tint = TextMid,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(8.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(Surface1)
                            .border(1.dp, CardBorder, RoundedCornerShape(8.dp))
                            .clickable { fullscreen = true }
                            .padding(6.dp),
                    )
                }
                s.error?.let {
                    Text("⚠ $it", color = RedInk, fontSize = 12.sp,
                        modifier = Modifier.padding(top = 8.dp))
                }
                s.scan!!.verdict?.let {
                    Column(modifier = Modifier.padding(top = 14.dp)) { VerdictCard(it) }
                }
            }
        }
    }
}

@Composable
private fun LayerControls(
    layers: LayerVis,
    density: Density,
    onToggle: (ChartViewModel.Layer) -> Unit,
    onDensity: (Density) -> Unit,
) {
    Column(modifier = Modifier.padding(top = 12.dp)) {
        // layer visibility chips
        Row(
            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            LayerChip("S/R", layers.sr) { onToggle(ChartViewModel.Layer.SR) }
            LayerChip("Demand/Supply", layers.supplyDemand) { onToggle(ChartViewModel.Layer.SUPPLY_DEMAND) }
            LayerChip("FVG", layers.fvg) { onToggle(ChartViewModel.Layer.FVG) }
            LayerChip("Trendlines", layers.trendlines) { onToggle(ChartViewModel.Layer.TRENDLINES) }
        }
        // density segmented control
        Row(
            modifier = Modifier
                .padding(top = 8.dp)
                .clip(RoundedCornerShape(8.dp))
                .border(1.dp, CardBorder, RoundedCornerShape(8.dp)),
        ) {
            DensitySeg("Key", density == Density.KEY) { onDensity(Density.KEY) }
            DensitySeg("More", density == Density.MORE) { onDensity(Density.MORE) }
            DensitySeg("All", density == Density.ALL) { onDensity(Density.ALL) }
        }
    }
}

@Composable
private fun LayerChip(label: String, on: Boolean, onClick: () -> Unit) {
    Text(
        label,
        color = if (on) Lime else TextMid,
        fontSize = 12.sp,
        fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
        modifier = Modifier
            .clip(RoundedCornerShape(20.dp))
            .background(if (on) Lime.copy(alpha = 0.12f) else Surface1)
            .border(1.dp, if (on) Lime.copy(alpha = 0.6f) else CardBorder, RoundedCornerShape(20.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 13.dp, vertical = 7.dp),
    )
}

@Composable
private fun DensitySeg(label: String, on: Boolean, onClick: () -> Unit) {
    Text(
        label,
        color = if (on) LimeInk else TextMid,
        fontSize = 12.sp,
        fontWeight = if (on) FontWeight.Bold else FontWeight.Normal,
        modifier = Modifier
            .background(if (on) Lime else Surface1)
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 7.dp),
    )
}

@Composable
private fun StructureRow(st: Structure) {
    val (bg, ink) = when (st.state) {
        "UPTREND" -> GreenBg to GreenInk
        "DOWNTREND" -> RedBg to RedInk
        "CONSOLIDATION" -> AmberBg to AmberInk
        else -> Surface2 to TextMid
    }
    Row(modifier = Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(
            "${st.state.lowercase().replaceFirstChar { it.uppercase() }}  ${(st.confidence * 100).toInt()}%",
            color = ink, fontSize = 12.sp, fontWeight = FontWeight.Bold,
            modifier = Modifier
                .clip(RoundedCornerShape(20.dp))
                .background(bg)
                .padding(horizontal = 12.dp, vertical = 5.dp),
        )
    }
}

@Composable
private fun Box_Loading() {
    Row(
        modifier = Modifier.fillMaxWidth().padding(top = 40.dp),
        horizontalArrangement = Arrangement.Center,
    ) { CircularProgressIndicator() }
}

@Composable
private fun ErrorBox(msg: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 30.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(RedBg)
            .padding(20.dp),
    ) {
        Text(msg, color = RedInk, fontSize = 13.sp)
        Text(
            "Tap to retry",
            color = RedInk, fontWeight = FontWeight.Bold, fontSize = 13.sp,
            modifier = Modifier.padding(top = 10.dp).clickable { onRetry() },
        )
    }
}

private fun displayFor(s: ChartUiState, id: String): String =
    s.pairs.firstOrNull { it.id == id }?.display ?: id

private fun fmt(v: Double): String = if (kotlin.math.abs(v) >= 1000) String.format("%.2f", v) else String.format("%.5f", v)
