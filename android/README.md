# Homebeat Android app

Kotlin + Jetpack Compose (Material 3, dark), Media3, WorkManager, Room. Package `org.homebeat.app`,
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

## Android Auto

Auto support is plain AndroidX Media3: `PlaybackService` is a `MediaLibraryService` (also bound through the
legacy `android.media.browse.MediaBrowserService` action), the tree lives in `playback/AutoLibrary.kt`, and
cover art is served to other processes by `playback/ArtProvider.kt` (`content://<applicationId>.art/album/<id>?size=256`,
fetched with the app's pinned client, cached in `cacheDir/art`, read access granted per URI to the browser).
Auto only accepts browsable root children, at most the limit it sends (usually 4), so the root it gets is
For you · Library · Radio · Downloads; browsers without a limit get the flat 7-section root.

Testing with the Desktop Head Unit (sideloaded APK):

1. Phone: Android Auto settings → tap *Version* 10× → ⋮ → *Developer settings* → enable *Unknown sources*;
   ⋮ → *Start head unit server*.
2. PC: SDK Manager → *Android Auto Desktop Head Unit Emulator*; `adb forward tcp:5277 tcp:5277`;
   `~/Android/Sdk/extras/google/auto/desktop-head-unit`.
3. Check: Homebeat appears in the media app list; tabs For you / Library / Radio / Downloads; Albums is a
   grid with covers; tapping a song plays its album from that song (queue shows the album); For you →
   *Play something for now* starts the smart shuffle; a *Continue listening* entry resumes at the saved
   position; search (magnifier) shows Songs / Albums / Artists; voice (`m` in DHU, or "Hey Google, play
   <artist> on Homebeat") starts artist radio; now-playing and the lock screen show the cover.
4. Offline: turn Wi-Fi and data off → Library rows say "Can't reach your music server", Downloads still
   plays; turn the network back on → the open tab reloads by itself.
5. If the tree is empty: `adb logcat -s MLSLegacyStub PlaybackService` (root / browse errors are logged there).

## Layout

| Package | What |
| --- | --- |
| `net` | `ServerRouter` (LAN probe on network change → LAN / tunnel / offline), `HttpClients` (pinned-cert LAN client, CF-Access tunnel client, bearer auth), `RoutedCallFactory` (one `Call.Factory` for Retrofit, Coil and ExoPlayer; rewrites `http://nas.invalid/api/v1/…` to the active route) |
| `pairing` | QR payload (docs/pairing.md), `/pair`, `SecureStore` (Tink AES-GCM, key wrapped by Android Keystore) |
| `data` | Retrofit API + models hand-written from `api/openapi.yaml`, Room (event queue, downloads, response cache), DataStore settings |
| `playback` | `PlaybackService` (MediaSessionService + ExoPlayer; local file else `/stream/{id}`), `FeedbackTracker` |
| `feedback` | play/skip classification (80 % rule), event recorder, batch uploader |
| `sync` | `SyncWorker` (manifest diff, resumable downloads, storage limit, LAN-only unless allowed), scheduler |
| `voice` | Mic button in search: offline speech-to-text with Vosk (model downloaded on first use, ~40 MB, kept in app storage), text sent to `POST /me/voice/command`, `local` answers played/controlled on this phone |
| `local` | Music on this phone (Settings, off by default): MediaStore scan (optionally chosen folders) into normal `Track`s with `local:` ids, shown in Library → On this phone and in Search. They play from MediaStore, also offline, and never reach the server (no events, queues, playlists, Cast or Connect) |
| `ui` | Today, Favourites, Library (artists → albums → tracks + search), Now Playing, Discover, Settings, Pairing |

## Licences

All dependencies are open source (Apache-2.0, plus protobuf-javalite BSD-3 via Tink, JNA Apache-2.0/LGPL-2.1
via Vosk, and JUnit EPL-1.0 for tests); see the header of `gradle/libs.versions.toml`. No Play Services, Firebase or ML Kit.
