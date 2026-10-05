package com.tradeguard.app.ui.markets

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Text
import androidx.compose.material3.OutlinedTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.tradeguard.app.ui.chart.ChartViewModel
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.TextMid
import com.tradeguard.app.ui.theme.UpGreen

@Composable
fun MarketsScreen(vm: ChartViewModel) {
    val s by vm.state.collectAsStateWithLifecycle()
    var query by remember { mutableStateOf("") }
    val visible = s.pairs.filter { pair ->
        query.isBlank() || pair.display.contains(query, true) || pair.code.contains(query, true)
    }.let { list -> if (query.isBlank()) list.filter { it.code.startsWith("R_") || it.code.startsWith("1HZ") }.take(12) else list }
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Text("Markets", fontWeight = FontWeight.Bold, fontSize = 24.sp, color = Ink)
        Text("Deriv public quotes · ${s.quoteStatus}", color = if (s.quoteStatus == "live") UpGreen else TextMid,
            fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp, bottom = 14.dp))
        OutlinedTextField(value = query, onValueChange = { query = it }, singleLine = true,
            placeholder = { Text("Search markets or symbols") }, modifier = Modifier.fillMaxWidth())
        if (s.pairs.isEmpty()) Text("Loading active instruments…", color = TextMid)
        else LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 12.dp)) {
            items(visible, key = { it.id }) { pair ->
                val selected = pair.id == s.selectedPair
                Row(Modifier.fillMaxWidth().clickable { vm.selectPair(pair.id) }.padding(14.dp),
                    horizontalArrangement = Arrangement.SpaceBetween) {
                    Column { Text(pair.display, color = Ink, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
                        Text(pair.code.ifBlank { pair.id }, color = TextMid, fontSize = 11.sp) }
                    if (selected && s.liveQuote != null) Text(formatQuote(s.liveQuote!!.quote), color = UpGreen, fontWeight = FontWeight.Bold)
                    else Text("Select", color = TextMid, fontSize = 12.sp)
                }
            }
        }
    }
}

private fun formatQuote(value: Double) = String.format("%.5f", value)
