//! nas-music desktop: a Tauri v2 shell around the web player (`../web/dist`).
//! Extras over the browser: tray icon (play/pause, next, previous, show, quit), global media keys,
//! a native notification on track change, single instance, and the window size/position remembered.
//! Optional: Discord "Listening to" status (`discord.rs`, off by default).
//! The web app listens for `nm-control` events and calls `track_changed`, `discord_configure`, `discord_update`.

mod discord;

use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, State, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Shortcut, ShortcutState};
use tauri_plugin_notification::NotificationExt;

const TRAY_ID: &str = "main-tray";

fn control(app: &AppHandle, action: &str) {
    let _ = app.emit("nm-control", action);
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

/// Called by the web app whenever a new track starts.
#[tauri::command]
fn track_changed(app: AppHandle, title: String, artist: String, album: String) {
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_tooltip(Some(format!("{title} — {artist}")));
    }
    // Only notify when the window isn't in front (no point otherwise).
    let focused = app
        .get_webview_window("main")
        .and_then(|w| w.is_focused().ok())
        .unwrap_or(false);
    if !focused {
        let _ = app
            .notification()
            .builder()
            .title(&title)
            .body(format!("{artist} · {album}"))
            .show();
    }
}

/// Turn the Discord status on (with the user's Discord application ID) or off (`None`).
#[tauri::command]
fn discord_configure(discord: State<discord::Discord>, app_id: Option<String>) {
    let _ = discord.0.send(discord::Msg::Configure(app_id));
}

/// What's playing now, or `None` when paused/stopped (the status is cleared).
#[tauri::command]
fn discord_update(discord: State<discord::Discord>, now: Option<discord::NowPlaying>) {
    let _ = discord.0.send(discord::Msg::Update(now));
}

pub fn run() {
    tauri::Builder::default()
        // Must be first: a second launch focuses the running window instead.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    let action = match shortcut.key {
                        Code::MediaPlayPause => "toggle",
                        Code::MediaTrackNext => "next",
                        Code::MediaTrackPrevious => "prev",
                        Code::MediaStop => "pause",
                        _ => return,
                    };
                    control(app, action);
                })
                .build(),
        )
        .manage(discord::Discord::spawn())
        .invoke_handler(tauri::generate_handler![track_changed, discord_configure, discord_update])
        .setup(|app| {
            // Global media keys. Registration can fail (e.g. another player owns them, or Wayland);
            // the web player's Media Session still handles keys while it has focus.
            for code in [Code::MediaPlayPause, Code::MediaTrackNext, Code::MediaTrackPrevious, Code::MediaStop] {
                if let Err(e) = app.global_shortcut().register(Shortcut::new(None, code)) {
                    eprintln!("media key {code:?} not registered: {e}");
                }
            }

            let toggle = MenuItem::with_id(app, "toggle", "Play / Pause", true, None::<&str>)?;
            let next = MenuItem::with_id(app, "next", "Next", true, None::<&str>)?;
            let prev = MenuItem::with_id(app, "prev", "Previous", true, None::<&str>)?;
            let show = MenuItem::with_id(app, "show", "Show nas-music", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(app)?;
            let menu = Menu::with_items(app, &[&toggle, &next, &prev, &sep, &show, &quit])?;

            TrayIconBuilder::with_id(TRAY_ID)
                .icon(app.default_window_icon().cloned().expect("bundle icon"))
                .tooltip("nas-music")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, ev| match ev.id().as_ref() {
                    "toggle" | "next" | "prev" => control(app, ev.id().as_ref()),
                    "show" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, ev| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = ev {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;
            Ok(())
        })
        // Closing the window keeps music playing in the tray; "Quit" in the tray menu exits.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running nas-music");
}
