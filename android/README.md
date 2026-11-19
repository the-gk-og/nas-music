# nas-music Android app

Kotlin + Jetpack Compose (Material 3, dark), Media3, WorkManager, Room. Package `org.nasmusic.app`,
minSdk 26, targetSdk 35 (compileSdk 36 because current AndroidX needs it).

## Build

```bash
cd android
./gradlew assembleFossDebug testFossDebugUnitTest        # default: no Google code
./gradlew assembleFullDebug testFullDebugUnitTest        # + Google Cast button
# APKs: app/build/outputs/apk/{foss,full}/debug/app-{foss,full}-debug.apk
scripts/check-no-gms.sh app/build/outputs/apk/foss/debug/app-foss-debug.apk
```

Two product flavours, same applicationId (either installs as an update over the other):
- `foss` (default): no Google Play Services. Speakers (Chromecast, DLNA, Home Assistant, Snapcast) come from
  the server and show in the Devices sheet as Connect devices; the server plays on them.
- `full`: adds `play-services-cast-framework` (the one owner-approved non-free exception, `fullImplementation`
  only, code in `app/src/full/`) for a Google Cast button in the Devices sheet. It casts the phone's queue
  straight to a Chromecast with signed URLs from `POST /cast/sign`; this is outside Connect. Pinned to Cast
  22.3.1: 23.x pulls mediarouter 1.9, which needs AGP 9.1 / compileSdk 37.

Needs the Android SDK (platform 36 + build-tools) at `ANDROID_HOME` or `sdk.dir` in `local.properties`.
Gradle runs its daemon on JDK 21 (`gradle/gradle-daemon-jvm.properties`) and compiles with a JDK 21
toolchain; both are auto-downloaded via foojay if missing, so the launcher JDK can be anything ≥ 17.

One-time SDK setup without Android Studio:

```bash
mkdir -p ~/Android/Sdk/cmdline-tools && cd ~/Android/Sdk/cmdline-tools
curl -LO https://dl.google.com/android/repository/commandlinetools-linux-16111833_latest.zip
unzip commandlinetools-linux-*_latest.zip && mv cmdline-tools latest
yes | latest/bin/sdkmanager --licenses
latest/bin/sdkmanager "platforms;android-36" "build-tools;36.0.0" "platform-tools"
```

## Layout

| Package | What |
| --- | --- |
| `net` | `ServerRouter` (LAN probe on network change → LAN / tunnel / offline), `HttpClients` (pinned-cert LAN client, CF-Access tunnel client, bearer auth), `RoutedCallFactory` (one `Call.Factory` for Retrofit, Coil and ExoPlayer; rewrites `http://nas.invalid/api/v1/…` to the active route) |
| `pairing` | QR payload (docs/pairing.md), `/pair`, `SecureStore` (Tink AES-GCM, key wrapped by Android Keystore) |
| `data` | Retrofit API + models hand-written from `api/openapi.yaml`, Room (event queue, downloads, response cache), DataStore settings |
| `playback` | `PlaybackService` (MediaSessionService + ExoPlayer; local file else `/stream/{id}`), `FeedbackTracker` |
| `feedback` | play/skip classification (80 % rule), event recorder, batch uploader |
| `sync` | `SyncWorker` (manifest diff, resumable downloads, storage limit, LAN-only unless allowed), scheduler |
| `ui` | Today, Favourites, Library (artists → albums → tracks + search), Now Playing, Discover, Settings, Pairing |

## Licences

All dependencies are open source (Apache-2.0, plus protobuf-javalite BSD-3 via Tink and JUnit EPL-1.0
for tests); see the header of `gradle/libs.versions.toml`. No Play Services, Firebase or ML Kit.
