// TradeGuard Android — app module.
// Derived from archive/dependencies.gradle.kts, trimmed to what the first APK
// needs. Room + Firebase (FCM) are deliberately deferred (see mobile_app/PLAN.md):
// they require KSP codegen and a google-services.json, neither needed for the
// 4-tab shell + live Chart. DataStore covers Settings persistence for now.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "com.tradeguard.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.tradeguard.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        // The backend base URL. Physical phone reaches the Mac by LAN IP, NOT
        // localhost. Override per build without editing code:
        //   ./gradlew assembleDebug -PtgApiBase=http://192.168.1.9:8000/
        val apiBase = (project.findProperty("tgApiBase") as String?)
            ?: "http://192.168.1.5:8000/"
        buildConfigField("String", "API_BASE_URL", "\"$apiBase\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

dependencies {
    // ---- UI: Jetpack Compose (BOM aligns all compose versions) --------------
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.navigation:navigation-compose:2.8.0")   // 4 tabs
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.5")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.5")

    // ---- Networking: REST to FastAPI ----------------------------------------
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.jakewharton.retrofit:retrofit2-kotlinx-serialization-converter:1.0.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")             // + WebSocket later
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")

    // ---- Async --------------------------------------------------------------
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // ---- Local settings persistence -----------------------------------------
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // ---- Testing ------------------------------------------------------------
    testImplementation("junit:junit:4.13.2")
}
