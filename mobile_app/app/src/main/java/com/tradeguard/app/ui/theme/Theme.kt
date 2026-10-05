package com.tradeguard.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// ---------------------------------------------------------------------------
// Palette borrowed from the Bittap dark theme: near-black canvas, warm charcoal
// cards with a faint gold border, a bright lime accent, white primary buttons.
// ---------------------------------------------------------------------------
val Bg = Color(0xFF0B0B0D)          // app background, near-black
val Surface1 = Color(0xFF15151A)    // cards / chips
val Surface2 = Color(0xFF1C1913)    // warm elevated surface
val CardBorder = Color(0xFF2E2A22)  // subtle gold-brown hairline
val Divider = Color(0xFF26262C)

val Lime = Color(0xFFC6F53C)        // bright accent (active states, highlights)
val LimeInk = Color(0xFF0B0B0D)     // text/icon on lime
val Gold = Color(0xFFC9A96A)        // tan accent (labels, dividers)

val TextHi = Color(0xFFF5F5F7)      // primary text
val TextMid = Color(0xFF9A9AA2)     // secondary text
val TextLo = Color(0xFF6B6B73)      // tertiary

// Trading — tuned to read on black.
val UpGreen = Color(0xFF34D399)
val DownRed = Color(0xFFF87171)
val FvgPurple = Color(0xFFB98CFF)

// Verdict states — dark card + bright ink.
val WaitBg = Color(0xFF262011); val WaitInk = Color(0xFFFBBF24); val WaitLine = Color(0xFF4A3D18)
val EnterBg = Color(0xFF0E241A); val EnterInk = Color(0xFF34D399); val EnterLine = Color(0xFF1C4736)
val AsideBg = Color(0xFF2A1414); val AsideInk = Color(0xFFF87171); val AsideLine = Color(0xFF4C2020)

// Back-compat aliases (older screens referenced these names).
val Ink = TextHi
val Muted = TextMid
val Line = Divider
val Indigo = Lime
val AmberBg = WaitBg; val AmberInk = WaitInk; val AmberLine = WaitLine
val GreenBg = EnterBg; val GreenInk = EnterInk; val GreenLine = EnterLine
val RedBg = AsideBg; val RedInk = AsideInk

private val DarkColors = darkColorScheme(
    primary = Lime,
    onPrimary = LimeInk,
    secondary = Gold,
    background = Bg,
    onBackground = TextHi,
    surface = Surface1,
    onSurface = TextHi,
    surfaceVariant = Surface2,
    onSurfaceVariant = TextMid,
    outline = CardBorder,
    secondaryContainer = Surface2,     // nav selected pill
    onSecondaryContainer = Lime,
)

@Composable
fun TradeGuardTheme(content: @Composable () -> Unit) {
    // The borrowed look is intrinsically dark — commit to it rather than tracking
    // the system light/dark setting.
    MaterialTheme(colorScheme = DarkColors, content = content)
}
