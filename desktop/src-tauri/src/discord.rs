//! Optional Discord "Listening to …" status (off by default; Settings → Desktop app).
//!
//! Talks only to the Discord desktop client on this computer, over its local IPC socket
//! (`discord-rich-presence`, MIT). Nothing is sent anywhere if the setting is off or Discord isn't running.
//! Discord requires an application ID for this, which the user pastes into Settings (see desktop/README.md).
//!
//! A background thread owns the connection: the web app sends `discord_configure` / `discord_update`, and the
//! thread connects lazily, retries every 15 s while Discord is closed, and clears the status when playback
//! pauses or stops.

use std::sync::mpsc::{channel, RecvTimeoutError, Sender};
use std::thread;
use std::time::Duration;

use discord_rich_presence::{
    activity::{Activity, ActivityType, Assets, StatusDisplayType, Timestamps},
    DiscordIpc, DiscordIpcClient,
};
use serde::Deserialize;

const RETRY: Duration = Duration::from_secs(15);
/// Discord rejects activity text shorter than 2 or longer than 128 characters.
const MIN_LEN: usize = 2;
const MAX_LEN: usize = 128;
/// Name of an image uploaded to the Discord application's Rich Presence art assets (optional).
const LARGE_IMAGE: &str = "logo";

/// What's playing, from the web app. Times are Unix milliseconds.
#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct NowPlaying {
    pub title: String,
    pub artist: String,
    pub album: String,
    pub started_at_ms: Option<i64>,
    pub ends_at_ms: Option<i64>,
}

pub enum Msg {
    /// `None` turns the status off (and clears anything shown).
    Configure(Option<String>),
    /// `None` = nothing playing (paused, stopped, or playing on another device).
    Update(Option<NowPlaying>),
}

/// Held in Tauri state; commands push messages to the worker thread.
pub struct Discord(pub Sender<Msg>);

impl Discord {
    pub fn spawn() -> Self {
        let (tx, rx) = channel::<Msg>();
        thread::Builder::new()
            .name("discord-presence".into())
            .spawn(move || {
                let mut w = Worker::default();
                loop {
                    match rx.recv_timeout(RETRY) {
                        Ok(m) => w.handle(m),
                        Err(RecvTimeoutError::Timeout) => w.retry(),
                        Err(RecvTimeoutError::Disconnected) => break,
                    }
                }
                w.disconnect();
            })
            .expect("spawn discord thread");
        Discord(tx)
    }
}

#[derive(Default)]
struct Worker {
    app_id: Option<String>,
    client: Option<DiscordIpcClient>,
    current: Option<NowPlaying>,
}

impl Worker {
    fn handle(&mut self, m: Msg) {
        match m {
            Msg::Configure(id) => {
                let id = id.filter(|s| valid_app_id(s));
                if id != self.app_id {
                    self.disconnect();
                    self.app_id = id;
                }
            }
            Msg::Update(np) => {
                if np == self.current {
                    return;
                }
                self.current = np;
            }
        }
        self.push();
    }

    /// Discord may have started since the last try.
    fn retry(&mut self) {
        if self.client.is_none() && self.current.is_some() {
            self.push();
        }
    }

    fn push(&mut self) {
        let Some(id) = self.app_id.clone() else {
            return;
        };
        if self.client.is_none() {
            if self.current.is_none() {
                return; // nothing to show: don't even connect
            }
            let mut c = DiscordIpcClient::new(&id);
            if c.connect().is_err() {
                return; // Discord isn't running; retried on the timer
            }
            self.client = Some(c);
        }
        let c = self.client.as_mut().expect("connected");
        let res = match &self.current {
            Some(np) => c.set_activity(activity(np)),
            None => c.clear_activity(),
        };
        if res.is_err() {
            // Discord quit or restarted: drop the connection, reconnect on the next update or retry.
            let _ = c.close();
            self.client = None;
        }
    }

    fn disconnect(&mut self) {
        if let Some(mut c) = self.client.take() {
            let _ = c.clear_activity();
            let _ = c.close();
        }
    }
}

/// Discord application IDs are snowflakes: 17–20 digits.
pub fn valid_app_id(s: &str) -> bool {
    (17..=20).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit())
}

/// Fit text into Discord's 2–128 character limit (padding with a blank Braille cell, which Discord keeps).
pub fn fit(s: &str) -> String {
    let s = s.trim();
    let mut out: String = s.chars().take(MAX_LEN).collect();
    if s.chars().count() > MAX_LEN {
        out = out.chars().take(MAX_LEN - 1).collect::<String>() + "…";
    }
    while out.chars().count() < MIN_LEN {
        out.push('\u{2800}');
    }
    out
}

/// "Listening to <title>" / by <artist> / on <album>, with a progress bar when the times are known.
pub fn activity(np: &NowPlaying) -> Activity<'static> {
    let mut a = Activity::new()
        .activity_type(ActivityType::Listening)
        .status_display_type(StatusDisplayType::Details)
        .details(fit(&np.title))
        .state(fit(&format!("by {}", np.artist)))
        .assets(
            Assets::new()
                .large_image(LARGE_IMAGE)
                .large_text(fit(&np.album)),
        );
    if let (Some(start), Some(end)) = (np.started_at_ms, np.ends_at_ms) {
        if end > start {
            a = a.timestamps(Timestamps::new().start(start).end(end));
        }
    }
    a
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_ids() {
        assert!(valid_app_id("1234567890123456789"));
        assert!(!valid_app_id("12345"));
        assert!(!valid_app_id("12345678901234567x9"));
        assert!(!valid_app_id(""));
    }

    #[test]
    fn fits_discord_limits() {
        assert_eq!(fit("  Hello "), "Hello");
        assert_eq!(fit("X").chars().count(), 2);
        assert_eq!(fit("").chars().count(), 2);
        let long = "a".repeat(300);
        let f = fit(&long);
        assert_eq!(f.chars().count(), 128);
        assert!(f.ends_with('…'));
        // multi-byte text is cut on character boundaries
        assert_eq!(fit(&"é".repeat(200)).chars().count(), 128);
    }

    #[test]
    fn activity_shape() {
        let np = NowPlaying {
            title: "Song".into(),
            artist: "Band".into(),
            album: "Record".into(),
            started_at_ms: Some(1_000),
            ends_at_ms: Some(181_000),
        };
        let v = serde_json::to_value(activity(&np)).unwrap();
        assert_eq!(v["type"], 2);
        assert_eq!(v["details"], "Song");
        assert_eq!(v["state"], "by Band");
        assert_eq!(v["assets"]["large_text"], "Record");
        assert_eq!(v["timestamps"]["start"], 1_000);
        assert_eq!(v["timestamps"]["end"], 181_000);
        let paused_times = NowPlaying {
            started_at_ms: None,
            ..np
        };
        assert!(serde_json::to_value(activity(&paused_times))
            .unwrap()
            .get("timestamps")
            .is_none());
    }
}
