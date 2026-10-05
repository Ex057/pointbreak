package com.tradeguard.app.data.repo

import com.tradeguard.app.data.api.ApiModule
import com.tradeguard.app.data.api.DerivWebSocketClient
import com.tradeguard.app.data.model.HealthResponse
import com.tradeguard.app.data.model.ScanResult

// Thin repository over the REST API. A WebSocket live-tick path slots in here
// later (backend WS /stream) without the UI needing to change.
class MarketRepository(
    private val api: com.tradeguard.app.data.api.TradeGuardApi = ApiModule.api,
) {
    private val deriv = DerivWebSocketClient()
    suspend fun health(): HealthResponse = api.health()

    suspend fun verdict(pair: String, tf: String, minRr: Double = 2.0): ScanResult =
        api.verdict(pair, tf, minRr)

    suspend fun alerts(pair: String? = null) = api.alerts(pair)

    suspend fun stats(pair: String? = null) = api.stats(pair)

    suspend fun monitor() = api.monitor()

    suspend fun setMonitor(pair: String, tf: String) = api.setMonitor(pair, tf)

    fun derivTicks(symbol: String) = deriv.ticks(symbol)
    suspend fun derivHistory(symbol: String, granularity: Int = 900, count: Int = 120) = deriv.history(symbol, granularity, count)
    fun derivSymbols() = deriv.symbols()
}
