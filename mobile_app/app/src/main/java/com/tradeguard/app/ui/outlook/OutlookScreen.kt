package com.tradeguard.app.ui.outlook

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tradeguard.app.ui.chart.ChartViewModel
import com.tradeguard.app.ui.theme.AmberInk
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.TextMid
import com.tradeguard.app.ui.theme.UpGreen

@Composable
fun OutlookScreen(vm: ChartViewModel) {
    val s by vm.state.collectAsStateWithLifecycle()
    val verdict = s.scan?.verdict
    Column(Modifier.fillMaxSize().padding(18.dp)) {
        Text("Outlook", fontWeight = FontWeight.Bold, fontSize = 24.sp, color = Ink)
        Text("${s.scan?.pairDisplay ?: s.selectedPair} · ${s.selectedTf}", color = TextMid, modifier = Modifier.padding(top = 4.dp))
        Text(verdict?.headline ?: "Analysis is loading…", fontWeight = FontWeight.Bold, fontSize = 20.sp,
            color = verdictColor(verdict?.state), modifier = Modifier.padding(top = 24.dp))
        verdict?.reasons?.forEach { reason -> Text("• $reason", color = TextMid, modifier = Modifier.padding(top = 10.dp)) }
        Text("Projection, not confirmation", color = AmberInk, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 22.dp))
        Text(verdict?.enterBlockedBy?.let { "Wait for: $it" } ?: "Recalculate the path after the next meaningful close.", color = TextMid, modifier = Modifier.padding(top = 6.dp))
    }
}

private fun verdictColor(state: String?) = when (state) {
    "ENTER_READY", "BUY" -> UpGreen
    else -> AmberInk
}
