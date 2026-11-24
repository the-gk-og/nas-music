# nas-music for Wear OS (`android/wear`)

A standalone Wear OS app (Wear OS 3+, minSdk 30). It talks to the server only over the network. It does
not use Play Services or the Wear Data Layer, so it works without the phone app.

Build: `cd android && ./gradlew :wear:assembleDebug :wear:testDebugUnitTest`.
APK: `android/wear/build/outputs/apk/debug/wear-debug.apk`.
CI attaches `nasmusic-wear-v0.1.<n>.apk` (release, R8-shrunk) to each Android release.

## What it does
- **Sign in**: enter the server address. The watch probes `/server-info` and asks you to trust the cert
  fingerprint if it is self-signed (trust on first use, same as the phone). Then either:
  - enter a one-time **pair code** from the phone (Account → Add a phone) or the web player
    (Account → Pair a phone with a code), or
  - sign in with **username + password**.

  Both send `deviceKind: "watch"`. Credentials go in the same Tink/Keystore `SecureStore` the phone uses.
- **Routing**: same as the phone. It uses the LAN with the pinned cert when `/health` answers within 1 s,
  otherwise the tunnel with CF Access headers.
- **Connect**: the watch says `hello` with `kind: "watch", canPlay: true`.
  - When another device is active, the watch is its remote: now playing with cover, play/pause, skip,
    volume (crown/bezel or ±), queue and "Play on…".
  - When nothing else is active, it plays itself and reports state.
  - The socket only stays open while the UI is on screen, while playing, or briefly for the tile.
- **Standalone playback**: ExoPlayer + MediaSession.
  - Playback is held back until Bluetooth headphones are connected (`setSuppressPlaybackOnUnsuitableOutput`);
    until then it opens the Wear Bluetooth picker.
  - It streams Opus (64/96/128 kbps, default 96), or plays the downloaded file.
  - Playing a list creates a server queue, so other devices can take over.
- **Offline**: on a playlist, album or Favourites, tap "Download to watch".
  - A WorkManager job asks for Wi-Fi explicitly (`requestNetwork`), binds OkHttp to it, and downloads Opus.
  - A list is kept as a prefix that fits within min(storage limit, free space − 500 MB). Files no longer
    wanted are deleted.
  - It refreshes nightly while charging. Wi-Fi only is the default and can be switched off in Settings.
- **Tile**: shows cover, title and "on <device>", with prev / play-pause / next.
  - When nothing is loaded, ▶ resumes the last queue on the watch.
  - Buttons go through an invisible trampoline activity, because tiles can only launch activities.
- **Sleep detection** (opt-in, Settings → Sleep detection): pauses when you doze off.
  - Turn on "Pause when I fall asleep", then tap the moon chip on the home menu at bedtime. With "Start by
    itself at night" (on by default) it also starts when you open the app between 9 pm and 5 am while
    something it should pause is playing.
  - It reads the accelerometer and heart-rate sensor (raw Android sensors, no Health Services) in 30 s epochs
    (`sleep/SleepDetector.kt`). Asleep = still for 10 min and the 5-min median heart rate is 7 % below the
    median from while you settled down. Without heart rate (permission refused or no sensor), 20 min still.
    Heart rate that never drops still counts after 45 min of complete stillness.
  - It then fades out over 30 s and pauses, here or on the phone/speaker the watch is a remote for, and puts
    the volume back for next time. Moving your wrist during the fade cancels it. Pausing saves the position as
    usual, and with "Go back to where I dozed off" a podcast or audiobook is moved back to where it was when you
    last moved (at most 30 min).
  - "Podcasts and audiobooks only" is on by default; switch it off to pause music too.
  - It runs as a "health" foreground service with a wake lock, only while armed: it stops after pausing, after
    20 min with nothing playing, after 8 h, or from its notification.
- **Complication**: SHORT_TEXT / LONG_TEXT with the current title. Tapping it opens now playing.

## Shared code
At build time, `syncPhoneSharedSources` copies these from `android/app` into `build/generated/phoneShared`:
`net/`, `data/api/`, `data/FeedRepository.kt`, `connect/ConnectProtocol.kt`, `pairing/SecureStore.kt`,
`auth/AuthRepository.kt` and `sync/Downloader.kt`. The phone module is not modified. If those files start
depending on more phone code, this build breaks. In that case, extract a `:core` library module.
`org/nasmusic/app/diag/BugReportTypes.kt` here stands in for the phone's `diag/BugReport.kt`, which needs
the phone's BuildConfig.
