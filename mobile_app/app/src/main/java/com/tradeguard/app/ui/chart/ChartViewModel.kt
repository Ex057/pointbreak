package com.tradeguard.app.ui.chart

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tradeguard.app.data.model.PairInfo
import com.tradeguard.app.data.model.ScanResult
import com.tradeguard.app.data.model.DerivTick
import com.tradeguard.app.data.repo.MarketRepository
import com.tradeguard.app.data.repo.LocalAnalysisEngine
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

data class ChartUiState(
    val loading: Boolean = false,
    val pairs: List<PairInfo> = emptyList(),
    val timeframes: List<String> = listOf("1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W"),
    val selectedPair: String = "xauusd",
    val selectedTf: String = "1H",
    val scan: ScanResult? = null,
    val error: String? = null,
    // View controls — client-side only, no refetch.
    val layers: LayerVis = LayerVis(),
    val density: Density = Density.KEY,
    val liveQuote: DerivTick? = null,
    val quoteStatus: String = "connecting",
)

class ChartViewModel(
    private val repo: MarketRepository = MarketRepository(),
) : ViewModel() {

    private val _state = MutableStateFlow(ChartUiState())
    val state: StateFlow<ChartUiState> = _state.asStateFlow()

    init { bootstrap() }

    private fun bootstrap() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val fallback = fallbackPairs()
                _state.value = _state.value.copy(pairs = fallback, selectedPair = "R_25", loading = false)
                val symbols = withTimeoutOrNull(10_000) { repo.derivSymbols().first { it.isNotEmpty() } } ?: emptyList()
                val pairs = symbols.mapNotNull { item ->
                    val code = item.underlyingSymbol.ifBlank { item.symbol }
                    if (code.isBlank()) null else PairInfo(code, item.underlyingSymbolName.ifBlank { code }, code, "Deriv", true)
                }.distinctBy { it.id }.ifEmpty { fallbackPairs() }
                val preferred = _state.value.selectedPair.takeIf { id -> pairs.any { it.id == id } }
                    ?: pairs.firstOrNull { it.id == "R_25" }?.id
                    ?: pairs.firstOrNull()?.id ?: "R_25"
                _state.value = _state.value.copy(
                    pairs = pairs,
                    selectedPair = preferred,
                )
                viewModelScope.launch { streamQuote(preferred) }
                viewModelScope.launch { load() }
            } catch (e: Exception) {
                _state.value = _state.value.copy(loading = false, error = reason(e))
            }
        }
    }

    fun selectPair(id: String) {
        if (id == _state.value.selectedPair) return
        _state.value = _state.value.copy(selectedPair = id)
        viewModelScope.launch { streamQuote(id) }
        load()
    }

    fun selectTimeframe(tf: String) {
        if (tf == _state.value.selectedTf) return
        _state.value = _state.value.copy(selectedTf = tf)
        load()
    }

    // --- view controls (no network; the chart just recomposes) ---
    enum class Layer { SR, SUPPLY_DEMAND, FVG, TRENDLINES }

    fun toggleLayer(layer: Layer) {
        val v = _state.value.layers
        _state.value = _state.value.copy(
            layers = when (layer) {
                Layer.SR -> v.copy(sr = !v.sr)
                Layer.SUPPLY_DEMAND -> v.copy(supplyDemand = !v.supplyDemand)
                Layer.FVG -> v.copy(fvg = !v.fvg)
                Layer.TRENDLINES -> v.copy(trendlines = !v.trendlines)
            }
        )
    }

    fun setDensity(density: Density) {
        _state.value = _state.value.copy(density = density)
    }

    fun load() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val pair = _state.value.selectedPair
                val display = _state.value.pairs.firstOrNull { it.id == pair }?.display ?: pair
                val candles = withTimeoutOrNull(12_000) {
                    repo.derivHistory(pair, granularityFor(_state.value.selectedTf))
                } ?: throw IllegalStateException("Deriv candle history timed out")
                if (candles.isEmpty()) throw IllegalStateException("Deriv returned no candles")
                val scan = LocalAnalysisEngine.scan(pair, display, _state.value.selectedTf, candles)
                _state.value = _state.value.copy(loading = false, scan = scan)
            } catch (e: Exception) {
                _state.value = _state.value.copy(loading = false, error = reason(e))
            }
        }
    }

    private fun reason(e: Exception): String =
        e.message?.let { "Can't reach the backend: $it" }
            ?: "Can't reach the backend. Is it running on your Mac's LAN IP?"

    private fun granularityFor(tf: String) = when (tf.lowercase()) {
        "1m" -> 60
        "5m" -> 300
        "15m" -> 900
        "30m" -> 1800
        "1h", "1hr" -> 3600
        "4h" -> 14400
        "1d" -> 86400
        "1w" -> 604800
        else -> 900
    }

    private fun fallbackPairs() = listOf("R_10", "R_25", "R_50", "R_75", "R_100").map {
        PairInfo(it, "Volatility ${it.removePrefix("R_")}", it, "Synthetic", true)
    }

    private suspend fun streamQuote(pair: String) {
        val symbol = _state.value.pairs.firstOrNull { it.id == pair }?.code
            ?.takeIf { it.isNotBlank() } ?: pair
        runCatching {
            _state.value = _state.value.copy(quoteStatus = "connecting")
            repo.derivTicks(symbol).collect { tick ->
                _state.value = _state.value.copy(liveQuote = tick, quoteStatus = "live")
            }
        }.onFailure {
            _state.value = _state.value.copy(quoteStatus = "unavailable")
        }
    }
}
