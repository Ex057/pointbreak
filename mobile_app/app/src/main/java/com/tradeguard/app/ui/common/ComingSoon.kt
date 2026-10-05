package com.tradeguard.app.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tradeguard.app.ui.theme.AmberBg
import com.tradeguard.app.ui.theme.AmberInk
import com.tradeguard.app.ui.theme.Ink
import com.tradeguard.app.ui.theme.Muted

/** Placeholder for tabs whose backend isn't built yet — honest about what's coming. */
@Composable
fun ComingSoon(title: String, blurb: String, waitingOn: String) {
    Column(modifier = Modifier.fillMaxSize().padding(20.dp)) {
        Text(title, fontWeight = FontWeight.Bold, fontSize = 22.sp, color = Ink)
        Text(blurb, color = Muted, fontSize = 14.sp, modifier = Modifier.padding(top = 12.dp))
        Text(
            waitingOn,
            color = AmberInk, fontSize = 12.sp,
            modifier = Modifier
                .padding(top = 20.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(AmberBg)
                .padding(horizontal = 12.dp, vertical = 8.dp),
        )
    }
}
