package com.tradeguard.app.data.api

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.tradeguard.app.BuildConfig
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import java.util.concurrent.TimeUnit

// Single place the base URL and JSON config live. BuildConfig.API_BASE_URL is
// set from the tgApiBase Gradle property (defaults to the Mac LAN IP), so
// switching networks never means editing code.
object ApiModule {

    private val json = Json {
        ignoreUnknownKeys = true   // backend may add fields; don't break the client
        coerceInputValues = true
    }

    internal val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // Scans wait on the live feed for up to ~6s server-side; give headroom.
        .readTimeout(20, TimeUnit.SECONDS)
        .addInterceptor(
            HttpLoggingInterceptor().apply {
                level = if (BuildConfig.DEBUG) HttpLoggingInterceptor.Level.BASIC
                else HttpLoggingInterceptor.Level.NONE
            }
        )
        .build()

    val api: TradeGuardApi by lazy {
        Retrofit.Builder()
            .baseUrl(BuildConfig.API_BASE_URL)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(TradeGuardApi::class.java)
    }
}
