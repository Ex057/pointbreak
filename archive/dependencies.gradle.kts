// =============================================================================
// TradeGuard — ANDROID dependencies  (app/build.gradle.kts)
// This is the mobile "requirements.txt". Paste the dependencies block into
// app/build.gradle.kts. Kotlin 2.x, minSdk 26, targetSdk latest.
//
// Note for Claude Code: version numbers below are known-good baselines —
// bump each to the latest stable when scaffolding (Android Studio's
// "Suggested version" lint or `gradle dependencyUpdates` will flag them).
// =============================================================================

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.devtools.ksp")            // Room codegen
    id("com.google.gms.google-services")     // Firebase (FCM)
}

dependencies {

    // ---- UI: Jetpack Compose (BOM keeps all compose versions aligned) ------
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.navigation:navigation-compose:2.7.7")   // 4 tabs
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.0")

    // ---- Chart: draw candles/zones on Compose Canvas (no heavy lib needed).
    // If you prefer a library, add Vico:
    // implementation("com.patrykandpatrick.vico:compose-m3:2.0.0")

    // ---- Networking: REST to FastAPI + native WebSocket for live ticks -----
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-kotlinx-serialization:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")              // + WebSocket
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")

    // ---- Async ---------------------------------------------------------------
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // ---- Local cache: Room (offline candles, cached verdicts, settings) -----
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    implementation("androidx.datastore:datastore-preferences:1.1.1")  // settings

    // ---- Push alerts: Firebase Cloud Messaging (locked-phone delivery) ------
    implementation(platform("com.google.firebase:firebase-bom:33.1.0"))
    implementation("com.google.firebase:firebase-messaging")

    // ---- Background: periodic sync/refresh when app is closed ---------------
    implementation("androidx.work:work-runtime-ktx:2.9.0")

    // ---- Testing -------------------------------------------------------------
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    androidTestImplementation(platform("androidx.compose:compose-bom:2024.06.00"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
