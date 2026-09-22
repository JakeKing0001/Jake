// Companion Mobile MVP (F7.2): app Android che parla con Companion Server (F7.1) - nessuna copia lato mobile di
// ConversationState/PolicyEngine/TaskAgent (vedi core/jake_core.py), vedi il docstring di JakeRepository.kt.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "com.jake.companion"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.jake.companion"
        // minSdk 26 (Android 8.0): TLS 1.2 di sistema, EncryptedSharedPreferences e Compose Material3 sono
        // tutti pienamente supportati senza librerie di compatibilita' aggiuntive da questa versione in su.
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0-mvp"
    }

    buildFeatures {
        compose = true
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    packaging {
        resources.excludes.add("META-INF/*.md")
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.2")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    // Storage sicuro del device_id/token (F7.2) - Android Keystore, nessuna cifratura scritta a mano.
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // HTTP + SSE (F7.1: /command, /pairing/*, /approvals/*, /devices/*, /status; F6: /events).
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")

    // Protocollo sul filo (HudEvent/TaskNotificationPayload/DTO REST) - rispecchia core/hud_protocol.py e
    // core/companion_server.py, mai un formato inventato lato mobile.
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")

    // Scansione QR per il pairing (F7.1.2/F7.2) - una sola dipendenza leggera invece di CameraX+ML Kit
    // per questo primo MVP; vedi ROADMAP_EXECUTION.md per il limite dichiarato (nessun generatore QR lato PC
    // ancora, questa e' solo la meta' "scansione" del flusso).
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")

    androidTestImplementation(platform("androidx.compose:compose-bom:2024.06.00"))
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
