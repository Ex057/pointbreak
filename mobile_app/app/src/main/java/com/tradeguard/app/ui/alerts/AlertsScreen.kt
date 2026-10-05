package com.tradeguard.app.ui.alerts

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tradeguard.app.data.model.AlertDto
import com.tradeguard.app.data.repo.MarketRepository
import com.tradeguard.app.ui.theme.CardBorder
import com.tradeguard.app.ui.theme.DownRed
import com.tradeguard.app.ui.theme.Gold
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.Lime
import com.tradeguard.app.ui.theme.Surface1
import com.tradeguard.app.ui.theme.Surface2
import com.tradeguard.app.ui.theme.TextMid
import com.tradeguard.app.ui.theme.UpGreen
import kotlinx.coroutines.delay

/**
 * Alerts tab (mockup M2). One card per armed zone with its state timeline,
 * live from the backend monitor. Polls every 6s.
 */
@Composable
fun AlertsScreen(repo: MarketRepository = remember { MarketRepository() }) {
    var alerts by remember { mutableStateOf<List<AlertDto>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loaded by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        while (true) {
            try {
                alerts = repo.alerts().alerts
                error = null
            } catch (e: Exception) {
                error = e.message ?: "can't reach backend"
            }
            loaded = true
            delay(6000)
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Alerts", fontWeight = FontWeight.Bold, fontSize = 22.sp, color = Ink)
            val active = alerts.count { it.state in LIVE }
            if (active > 0) {
                Text(
                    "$active active", color = Lime, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .padding(start = 10.dp)
                        .clip(RoundedCornerShape(20.dp))
                        .background(Lime.copy(alpha = 0.12f))
                        .padding(horizontal = 10.dp, vertical = 4.dp),
                )
            }
        }

        when {
            !loaded -> Hint("Loading alerts…")
            error != null && alerts.isEmpty() -> Hint("⚠ $error")
            alerts.isEmpty() -> Hint("No alerts yet — the monitor arms zones as it scans your favourite pair.")
            else -> LazyColumn(
                modifier = Modifier.padding(top = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(alerts, key = { it.id }) { AlertCard(it) }
            }
        }
    }
}

private val LIVE = setOf("WATCHING", "TOUCHED", "SWEPT", "FILLED")

@Composable
private fun AlertCard(a: AlertDto) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(Surface1)
            .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
            .padding(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Text(
                "${a.pair.uppercase()} · ${a.timeframe}",
                color = Ink, fontWeight = FontWeight.Bold, fontSize = 15.sp,
            )
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                StateBadge(a.state)
            }
        }
        Text(
            "${a.side} at ${a.source} ${fmt(a.zoneMin)}–${fmt(a.zoneMax)} · entry ${fmt(a.entry)} · stop ${fmt(a.stop)}",
            color = TextMid, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp),
        )
        // live running result while the paper position is open
        if (a.state == "FILLED" && a.unrealizedR != null) {
            val up = a.unrealizedR >= 0
            Text(
                "▮ open · running ${if (up) "+" else ""}${fmt2(a.unrealizedR)}R  @ ${fmt(a.lastPrice ?: a.entry)}",
                color = if (up) UpGreen else DownRed,
                fontSize = 12.5.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
        // state timeline
        a.events.reversed().take(5).forEach { e ->
            Row(modifier = Modifier.padding(top = 8.dp)) {
                Column(
                    modifier = Modifier.size(9.dp).clip(CircleShape).background(dotColor(e.state)),
                ) {}
                Column(modifier = Modifier.padding(start = 10.dp)) {
                    Text(e.reason, color = Ink, fontSize = 12.5.sp)
                    Text("${e.state} · ${fmt(e.price)}", color = TextMid, fontSize = 11.sp)
                }
            }
        }
    }
}

@Composable
private fun StateBadge(state: String) {
    val (bg, ink) = when (state) {
        "FILLED", "TARGET" -> UpGreen.copy(alpha = 0.15f) to UpGreen
        "STOPPED", "INVALIDATED" -> DownRed.copy(alpha = 0.15f) to DownRed
        "SWEPT", "TOUCHED" -> Gold.copy(alpha = 0.15f) to Gold
        "EXPIRED" -> Surface2 to TextMid
        else -> Lime.copy(alpha = 0.12f) to Lime
    }
    Text(
        state.lowercase(), color = ink, fontSize = 11.sp, fontWeight = FontWeight.Bold,
        modifier = Modifier.clip(RoundedCornerShape(20.dp)).background(bg)
            .padding(horizontal = 10.dp, vertical = 4.dp),
    )
}

private fun dotColor(state: String): Color = when (state) {
    "FILLED", "TARGET" -> UpGreen
    "STOPPED", "INVALIDATED" -> DownRed
    "SWEPT", "TOUCHED" -> Gold
    "EXPIRED" -> TextMid
    else -> Lime
}

@Composable
private fun Hint(msg: String) {
    Text(msg, color = TextMid, fontSize = 13.sp, modifier = Modifier.padding(top = 30.dp))
}

private fun fmt(v: Double): String = String.format("%.2f", v)
private fun fmt2(v: Double): String = String.format("%.2f", v)
