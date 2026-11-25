// Standalone Wear OS app (docs/v3-ux.md §8 "Wear OS"). Talks to the server over the network only:
// no Play Services, no Wear Data Layer.
//
// Licences of the Wear-only dependencies below (everything else comes from the shared version catalog,
// whose header lists its licences):
//   androidx.wear.compose (material, foundation, navigation)  - Apache-2.0
//   androidx.wear.tiles, androidx.wear.protolayout             - Apache-2.0
//   androidx.wear.watchface complications-data-source          - Apache-2.0
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

val wearCompose = "1.5.0"
val wearTiles = "1.5.0"
val protolayout = "1.3.0"
val complications = "1.2.1"

kotlin {
    jvmToolchain(21)
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

/*
 * The phone app's networking, pairing storage, Connect protocol and API models are reused as-is, so the
 * watch speaks exactly the same contract (api/openapi.yaml) with the same LAN pinning / tunnel logic.
 * They are copied from android/app at build time (not moved), so the phone module stays untouched.
 * When android/app grows a library module for these, depend on that instead.
 */
val phoneShared = tasks.register<Sync>("syncPhoneSharedSources") {
    from(rootProject.file("app/src/main/java")) {
        include(
            "org/homebeat/app/net/**",
            "org/homebeat/app/data/api/**",
            "org/homebeat/app/eq/EqSettings.kt", // plain data classes referenced by HomebeatApi
            "org/homebeat/app/data/FeedRepository.kt",
            "org/homebeat/app/connect/ConnectProtocol.kt",
            "org/homebeat/app/pairing/SecureStore.kt",
            "org/homebeat/app/auth/AuthRepository.kt",
            "org/homebeat/app/sync/Downloader.kt",
            "org/homebeat/app/lyrics/LyricsTimeline.kt", // current lyric line on Now Playing
        )
    }
    into(layout.buildDirectory.dir("generated/phoneShared"))
}

android {
    namespace = "org.homebeat.wear"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.homebeat.wear"
        // Wear OS 3 (Galaxy Watch 4 and newer).
        minSdk = 30
        targetSdk = 35
        val buildNumber = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionCode = buildNumber
        versionName = "0.1.$buildNumber"
    }

    // Same release key as the phone app, from the environment only.
    val keystorePath = System.getenv("ANDROID_KEYSTORE_PATH")
    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            // R8 only drops unused code (mostly icons); our own classes are kept whole (proguard-rules.pro)
            // so serialization/Retrofit behave as in the debug build.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (keystorePath != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    sourceSets["main"].java.srcDir("build/generated/phoneShared")
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }
}

tasks.named("preBuild") { dependsOn(phoneShared) }

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)

    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material.icons)
    debugImplementation(libs.compose.ui.tooling)

    implementation("androidx.wear.compose:compose-material:$wearCompose")
    implementation("androidx.wear.compose:compose-foundation:$wearCompose")
    implementation("androidx.wear.compose:compose-navigation:$wearCompose")
    implementation("androidx.wear.tiles:tiles:$wearTiles")
    implementation("androidx.wear.protolayout:protolayout:$protolayout")
    implementation("androidx.wear.protolayout:protolayout-material:$protolayout")
    implementation("androidx.wear.protolayout:protolayout-expression:$protolayout")
    implementation("androidx.wear.watchface:watchface-complications-data-source-ktx:$complications")

    implementation(libs.media3.exoplayer)
    implementation(libs.media3.session)
    implementation(libs.media3.datasource.okhttp)
    implementation(libs.work.runtime.ktx)

    implementation(libs.okhttp)
    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.coroutines.guava)
    implementation(libs.coil.compose)
    implementation(libs.coil.network.okhttp)
    implementation(libs.tink.android)

    testImplementation(libs.junit)
}
