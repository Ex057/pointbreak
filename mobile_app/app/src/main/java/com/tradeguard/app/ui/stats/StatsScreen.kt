package com.tradeguard.app.ui.stats

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tradeguard.app.data.model.StatsResponse
import com.tradeguard.app.data.repo.MarketRepository
import com.tradeguard.app.ui.theme.CardBorder
import com.tradeguard.app.ui.theme.DownRed
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.Lime
import com.tradeguard.app.ui.theme.Surface1
import com.tradeguard.app.ui.theme.Surface2
import com.tradeguard.app.ui.theme.TextMid
import com.tradeguard.app.ui.theme.UpGreen
import kotlinx.coroutines.delay

/**
 * Stats tab (mockup M3). Trades / Win-rate / Avg-R tiles, win-rate bars per zone
 * type, and the paper-trading account balance — all live from journaled outcomes.
 */
@Composable
fun StatsScreen(repo: MarketRepository = remember { MarketRepository() }) {
    var data by remember { mutableStateOf<StatsResponse?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        while (true) {
            try { data = repo.stats(); error = null }
            catch (e: Exception) { error = e.message ?: "can't reach backend" }
            delay(8000)
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
    ) {
        Text("Your statistics", fontWeight = FontWeight.Bold, fontSize = 22.sp, color = Ink)

        val d = data
        if (d == null) {
            Text(error?.let { "⚠ $it" } ?: "Loading…", color = TextMid, fontSize = 13.sp,
                modifier = Modifier.padding(top = 30.dp))
            return
        }

        // paper account balance
        d.account?.let { acct ->
            val up = acct.balance >= acct.startingBalance
            Column(
                modifier = Modifier
                    .fillMaxWidth().padding(top = 14.dp)
                    .clip(RoundedCornerShape(12.dp)).background(Surface2)
                    .border(1.dp, CardBorder, RoundedCornerShape(12.dp)).padding(16.dp),
            ) {
                Text("Paper account · ${acct.pair.uppercase()}", color = TextMid, fontSize = 12.sp)
                Text(
                    "$${fmt(acct.balance)}",
                    color = if (up) UpGreen else DownRed,
                    fontWeight = FontWeight.Bold, fontSize = 28.sp,
                    modifier = Modifier.padding(top = 4.dp),
                )
                Text(
                    "started $${fmt(acct.startingBalance)} · ${acct.wins}W / ${acct.losses}L",
                    color = TextMid, fontSize = 12.sp,
                )
                // live equity including open positions' floating P&L
                if (d.open.isNotEmpty()) {
                    val fUp = d.floatingR >= 0
                    Text(
                        "${d.open.size} open · floating ${if (fUp) "+" else ""}${fmt(d.floatingR)}R " +
                            "(${if (d.floatingCash >= 0) "+" else ""}$${fmt(d.floatingCash)})",
                        color = if (fUp) UpGreen else DownRed,
                        fontSize = 12.5.sp, fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                    d.equityFloat?.let {
                        Text("live equity  $${fmt(it)}", color = TextMid, fontSize = 12.sp,
                            modifier = Modifier.padding(top = 2.dp))
                    }
                }
            }
        }

        // headline tiles
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Tile("Trades", d.stats.trades.toString(), Modifier.weight(1f))
            Tile("Win rate", "${fmt1(d.stats.winRate)}%", Modifier.weight(1f),
                if (d.stats.winRate >= 50) UpGreen else Ink)
            Tile("Avg result", "${if (d.stats.avgR >= 0) "+" else ""}${fmt(d.stats.avgR)}R",
                Modifier.weight(1f), if (d.stats.avgR >= 0) UpGreen else DownRed)
        }

        // win rate by zone type
        if (d.stats.byZone.isNotEmpty()) {
            Column(
                modifier = Modifier
                    .fillMaxWidth().padding(top = 14.dp)
                    .clip(RoundedCornerShape(12.dp)).background(Surface1)
                    .border(1.dp, CardBorder, RoundedCornerShape(12.dp)).padding(16.dp),
            ) {
                Text("Win rate by zone type", color = Ink, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                d.stats.byZone.forEach { z ->
                    Column(modifier = Modifier.padding(top = 12.dp)) {
                        Row(modifier = Modifier.fillMaxWidth()) {
                            Text(z.zoneType, color = Ink, fontSize = 13.sp)
                            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                                Text("${fmt1(z.winRate)}% · ${z.trades}", color = TextMid, fontSize = 12.sp)
                            }
                        }
                        // bar
                        Box(
                            modifier = Modifier.fillMaxWidth().height(8.dp).padding(top = 4.dp)
                                .clip(RoundedCornerShape(4.dp)).background(Surface2),
                        ) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth(fraction = (z.winRate / 100.0).toFloat().coerceIn(0f, 1f))
                                    .height(8.dp).clip(RoundedCornerShape(4.dp))
                                    .background(if (z.winRate >= 50) UpGreen else Lime),
                            ) {}
                        }
                    }
                }
            }
        } else {
            Text(
                "No closed trades yet. The monitor journals each alert to stop or target — " +
                    "stats fill in as they resolve.",
                color = TextMid, fontSize = 13.sp, modifier = Modifier.padding(top = 16.dp),
            )
        }
    }
}

@Composable
private fun Tile(label: String, value: String, modifier: Modifier = Modifier, valueColor: androidx.compose.ui.graphics.Color = Ink) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(10.dp)).background(Surface1)
            .border(1.dp, CardBorder, RoundedCornerShape(10.dp)).padding(12.dp),
    ) {
        Text(label, color = TextMid, fontSize = 11.sp)
        Text(value, color = valueColor, fontWeight = FontWeight.Bold, fontSize = 20.sp,
            modifier = Modifier.padding(top = 4.dp))
    }
}

private fun fmt(v: Double): String = String.format("%.2f", v)
private fun fmt1(v: Double): String = String.format("%.1f", v)
