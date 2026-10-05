package com.tradeguard.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.Muted

/**
 * Settings tab (mockup M4). Local-only for now (state held in `remember`);
 * DataStore persistence + backend sync is milestone A3. The controls mirror the
 * backend user_settings shape so they can bind later without a UI rewrite.
 */
@Composable
fun SettingsScreen() {
    var risk by remember { mutableFloatStateOf(1.0f) }
    var turtleSoup by remember { mutableStateOf(true) }
    var pauseNews by remember { mutableStateOf(true) }
    var notifyTouch by remember { mutableStateOf(true) }
    var notifyEntry by remember { mutableStateOf(true) }
    var notifyScan by remember { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
    ) {
        Text("Settings", fontWeight = FontWeight.Bold, fontSize = 22.sp, color = Ink)

        Section("Risk rules")
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Text("Risk per trade", color = Ink, fontSize = 14.sp)
            Text(String.format("  %.2f%%", risk), color = Muted, fontSize = 14.sp)
        }
        Slider(value = risk, onValueChange = { risk = it }, valueRange = 0.5f..2f)
        RowToggle("Turtle-soup gate before entry", turtleSoup) { turtleSoup = it }
        Text("Minimum R:R  1 : 2", color = Muted, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
        Text("Max concurrent positions: 3", color = Muted, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp))

        Section("News stand-aside")
        RowToggle("Pause before high-impact news", pauseNews) { pauseNews = it }
        Text("Window  30 min before", color = Muted, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))

        Section("Notifications")
        RowToggle("Zone touched", notifyTouch) { notifyTouch = it }
        RowToggle("Entry confirmed (sweep + reclaim)", notifyEntry) { notifyEntry = it }
        RowToggle("Every scan summary", notifyScan) { notifyScan = it }

        Text(
            "Local only for now — persistence & backend sync land in A3.",
            color = Muted, fontSize = 11.sp, modifier = Modifier.padding(top = 24.dp),
        )
    }
}

@Composable
private fun Section(title: String) {
    Text(title, fontWeight = FontWeight.Bold, fontSize = 15.sp, color = Ink,
        modifier = Modifier.padding(top = 22.dp, bottom = 4.dp))
}

@Composable
private fun RowToggle(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = Ink, fontSize = 14.sp)
        Switch(checked = value, onCheckedChange = onChange)
    }
}
