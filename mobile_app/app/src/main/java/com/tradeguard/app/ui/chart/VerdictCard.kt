package com.tradeguard.app.ui.chart

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tradeguard.app.data.model.Verdict
import com.tradeguard.app.ui.theme.AmberBg
import com.tradeguard.app.ui.theme.AmberInk
import com.tradeguard.app.ui.theme.DownRed
import com.tradeguard.app.ui.theme.GreenBg
import com.tradeguard.app.ui.theme.GreenInk
import com.tradeguard.app.ui.theme.Muted
import com.tradeguard.app.ui.theme.RedBg
import com.tradeguard.app.ui.theme.RedInk
import com.tradeguard.app.ui.theme.UpGreen

/**
 * The verdict is the hero. WAIT is amber and wordy; ENTER is green and terse;
 * STAND_ASIDE is red. Same language as the web app.
 */
@Composable
fun VerdictCard(verdict: Verdict, modifier: Modifier = Modifier) {
    val (bg, ink) = when (verdict.state) {
        "ENTER" -> GreenBg to GreenInk
        "STAND_ASIDE" -> RedBg to RedInk
        else -> AmberBg to AmberInk
    }

    Column(modifier = modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(bg)
                .padding(horizontal = 14.dp, vertical = 12.dp),
        ) {
            Text(verdict.headline, color = ink, fontWeight = FontWeight.Bold, fontSize = 16.sp)
            verdict.reasons.forEach { r ->
                Text(r, color = ink, fontSize = 12.5.sp, lineHeight = 16.sp,
                    modifier = Modifier.padding(top = 4.dp))
            }
            verdict.enterBlockedBy?.let {
                Text("🔒 ENTER gated: $it", color = ink.copy(alpha = 0.7f),
                    fontSize = 11.sp, modifier = Modifier.padding(top = 6.dp))
            }
        }

        verdict.plan?.let { plan ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Tile("Planned entry", fmt(plan.entry), Modifier.weight(1f))
                Tile("Stop", fmt(plan.stop), Modifier.weight(1f), DownRed)
                Tile("Target ${trimRr(plan.rr)}R", fmt(plan.target), Modifier.weight(1f), UpGreen)
                Tile("Size @ ${trimRr(plan.riskPct)}%", fmt(plan.size), Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun Tile(label: String, value: String, modifier: Modifier = Modifier, valueColor: Color = com.tradeguard.app.ui.theme.Ink) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(10.dp))
            .background(com.tradeguard.app.ui.theme.Surface2)
            .border(1.dp, com.tradeguard.app.ui.theme.CardBorder, RoundedCornerShape(10.dp))
            .padding(12.dp),
    ) {
        Text(label, color = Muted, fontSize = 11.sp)
        Text(value, color = valueColor, fontWeight = FontWeight.Bold, fontSize = 17.sp,
            modifier = Modifier.padding(top = 4.dp))
    }
}

private fun fmt(v: Double): String = String.format("%.2f", v)
private fun trimRr(v: Double): String =
    if (v == v.toLong().toDouble()) v.toLong().toString() else v.toString()
