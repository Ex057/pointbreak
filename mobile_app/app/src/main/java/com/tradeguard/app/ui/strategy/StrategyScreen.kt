package com.tradeguard.app.ui.strategy

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
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.TextMid

@Composable
fun StrategyScreen(vm: ChartViewModel) {
    val s by vm.state.collectAsStateWithLifecycle()
    val scan = s.scan
    Column(Modifier.fillMaxSize().padding(18.dp)) {
        Text("Strategy", fontWeight = FontWeight.Bold, fontSize = 24.sp, color = Ink)
        Text("Evidence-led setup review", color = TextMid, modifier = Modifier.padding(top = 4.dp))
        Text("1 · Direction", fontWeight = FontWeight.Bold, color = Ink, modifier = Modifier.padding(top = 26.dp))
        Text(scan?.structure?.reason ?: "Waiting for structure analysis.", color = TextMid, modifier = Modifier.padding(top = 6.dp))
        Text("2 · Entry gate", fontWeight = FontWeight.Bold, color = Ink, modifier = Modifier.padding(top = 20.dp))
        Text(scan?.verdict?.enterBlockedBy ?: "Check whether the trigger candle has closed.", color = TextMid, modifier = Modifier.padding(top = 6.dp))
        Text("3 · Invalidation", fontWeight = FontWeight.Bold, color = Ink, modifier = Modifier.padding(top = 20.dp))
        Text("The thesis is invalid when structure or the qualified zone fails. Reassess; do not chase.", color = TextMid, modifier = Modifier.padding(top = 6.dp))
    }
}
