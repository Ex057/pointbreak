package com.tradeguard.app.data.api

import com.tradeguard.app.data.model.DerivSymbol
import com.tradeguard.app.data.model.DerivTick
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import com.tradeguard.app.data.model.Candle
import kotlin.coroutines.resume

/** Public, unauthenticated Deriv market-data stream. No account token is used. */
class DerivWebSocketClient(
    private val client: OkHttpClient = ApiModule.client,
    private val endpoint: String = "wss://api.derivws.com/trading/v1/options/ws/public",
) {
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    fun symbols(): Flow<List<DerivSymbol>> = callbackFlow {
        val socket = open { message ->
            val root = json.parseToJsonElement(message).jsonObject
            if (root["msg_type"]?.toString()?.contains("active_symbols") == true) {
                val values = root["active_symbols"]?.jsonArray.orEmpty()
                    .mapNotNull { runCatching { json.decodeFromJsonElement(DerivSymbol.serializer(), it) }.getOrNull() }
                trySend(values)
                close()
            }
        }
        socket.send("{\"active_symbols\":\"brief\",\"req_id\":1}")
        awaitClose { socket.close(1000, "done") }
    }

    fun ticks(symbol: String): Flow<DerivTick> = callbackFlow {
        val socket = open { message ->
            val root = json.parseToJsonElement(message).jsonObject
            if (root["msg_type"]?.toString()?.contains("tick") == true) {
                root["tick"]?.let { tick ->
                    runCatching { json.decodeFromJsonElement(DerivTick.serializer(), tick) }
                        .onSuccess { trySend(it) }
                }
            }
        }
        socket.send(buildJsonObject { put("ticks", symbol); put("subscribe", 1); put("req_id", 2) }.toString())
        awaitClose { socket.close(1000, "done") }
    }

    suspend fun history(symbol: String, granularity: Int = 900, count: Int = 120): List<Candle> =
        suspendCancellableCoroutine { continuation ->
            val socket = open { message ->
                runCatching {
                    val root = json.parseToJsonElement(message).jsonObject
                    root["candles"]?.jsonArray.orEmpty().mapNotNull { item ->
                        val o = item.jsonObject
                        Candle(
                            time = o["epoch"]?.toString()?.toLongOrNull() ?: return@mapNotNull null,
                            open = o["open"]?.toString()?.toDoubleOrNull() ?: return@mapNotNull null,
                            high = o["high"]?.toString()?.toDoubleOrNull() ?: return@mapNotNull null,
                            low = o["low"]?.toString()?.toDoubleOrNull() ?: return@mapNotNull null,
                            close = o["close"]?.toString()?.toDoubleOrNull() ?: return@mapNotNull null,
                        )
                    }
                }.onSuccess { candles ->
                    if (continuation.isActive) continuation.resume(candles)
                }
            }
            socket.send(buildJsonObject {
                put("ticks_history", symbol)
                put("adjust_start_time", 1)
                put("count", count)
                put("end", "latest")
                put("granularity", granularity)
                put("style", "candles")
                put("req_id", 3)
            }.toString())
            continuation.invokeOnCancellation { socket.cancel() }
        }

    private fun open(onMessage: (String) -> Unit): WebSocket = client.newWebSocket(
        Request.Builder().url(endpoint).build(),
        object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) = onMessage(text)
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) = onMessage(bytes.utf8())
        },
    )
}
