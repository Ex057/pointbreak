package com.tradeguard.app.data.api

import com.tradeguard.app.data.model.AlertsResponse
import com.tradeguard.app.data.model.HealthResponse
import com.tradeguard.app.data.model.MonitorStatus
import com.tradeguard.app.data.model.ScanResult
import com.tradeguard.app.data.model.StatsResponse
import retrofit2.http.GET
import retrofit2.http.PUT
import retrofit2.http.Query

interface TradeGuardApi {
    @GET("health")
    suspend fun health(): HealthResponse

    // Query-based endpoint — pair ids like "eurusd" are path-safe, but the
    // backend also accepts display names here. min_rr defaults server-side.
    @GET("verdict")
    suspend fun verdict(
        @Query("pair") pair: String,
        @Query("tf") tf: String,
        @Query("min_rr") minRr: Double = 2.0,
    ): ScanResult

    @GET("alerts")
    suspend fun alerts(@Query("pair") pair: String? = null): AlertsResponse

    @GET("stats")
    suspend fun stats(@Query("pair") pair: String? = null): StatsResponse

    @GET("monitor")
    suspend fun monitor(): MonitorStatus

    @PUT("monitor")
    suspend fun setMonitor(@Query("pair") pair: String, @Query("tf") tf: String): MonitorStatus
}
