// Types for the nas-music HTTP API v1 (+ v2 additions from docs/v2.md §4/§5).
// Keep in sync with api/openapi.yaml.

export interface Artist {
  id: string;
  name: string;
  albumCount: number;
  mbid?: string | null;
}

export interface Album {
  id: string;
  title: string;
  artistId: string;
  artist: string;
  year?: number | null;
  trackCount: number;
  durationS?: number;
  hasArt?: boolean;
  mbid?: string | null;
}

export interface Track {
  id: string;
  title: string;
  artistId: string;
  artist: string;
  albumId: string;
  album: string;
  trackNo?: number | null;
  discNo?: number | null;
  year?: number | null;
  genre?: string | null;
  durationS: number;
  flacBytes?: number;
  starred: boolean;
  rating?: number | null;
}

export interface TrackInfo extends Track {
  fileName: string;
  folder: string;
  albumArtist?: string | null;
  composer?: string | null;
  lyricist?: string | null;
  comment?: string | null;
  codec?: string | null;
  bitrateKbps?: number | null;
  sampleRate?: number | null;
  bitsPerSample?: number | null;
  channels?: number | null;
  fileBytes?: number | null;
}

export type LyricsSource = 'embedded' | 'lrc_file' | 'user';

export interface LyricLine {
  timeMs?: number | null;
  text: string;
}

export interface Lyrics {
  trackId: string;
  synced: boolean;
  source: LyricsSource;
  lines: LyricLine[];
  text: string;
  canEdit?: boolean;
}

export interface MixTrack {
  track: Track;
  reason: string;
}

export interface Mix {
  id: string;
  kind: string;
  title: string;
  createdAt: string;
  durationS?: number;
  tracks: MixTrack[];
}

export interface Suggestion {
  id: string;
  artist: string;
  album: string;
  year?: number | null;
  hasCover?: boolean;
  hasPreview?: boolean;
  buyLinks?: { name: string; url: string }[];
  reason: string;
  status: 'new' | 'want' | 'not_interested' | 'have';
  createdAt?: string;
}

export type DeviceKind = 'android' | 'web' | 'desktop' | 'watch' | 'other';

export interface Session {
  userId: string;
  username: string;
  isAdmin: boolean;
  deviceId: string;
  deviceToken: string;
}

export interface Me {
  userId: string;
  username: string;
  isAdmin: boolean;
}

export interface ServerInfo {
  serverId: string;
  name: string;
  version: string;
  lanUrl?: string | null;
  tunnelUrl?: string | null;
  certSha256?: string | null;
  registration: 'invite';
}

export interface MyDevice {
  id: string;
  name: string;
  kind: DeviceKind;
  lastSeen?: string | null;
  online?: boolean;
}

export interface FolderListing {
  path: string;
  parent: string | null;
  folders: { name: string; path: string; trackCount: number }[];
  tracks: Track[];
}

export interface Genre {
  name: string;
  trackCount?: number;
}

export type SmartListKind = 'favourites' | 'recently_added' | 'recently_played' | 'most_played' | 'not_played';

export interface Playlist {
  id: string;
  name: string;
  trackCount?: number;
  durationS?: number;
  createdAt?: string;
  updatedAt?: string;
  tracks?: Track[];
}

export interface Queue {
  id: string;
  name: string;
  slot: number;
  currentIndex: number;
  positionMs: number;
  trackCount?: number;
  durationS?: number;
  updatedAt?: string;
  tracks?: Track[];
}

export interface Invite {
  code: string;
  url?: string;
  note?: string | null;
  createdAt?: string;
  expiresAt: string;
  usedBy?: string | null;
  usedAt?: string | null;
}

export interface AdminUser {
  id: string;
  username: string;
  isAdmin: boolean;
  disabled: boolean;
  createdAt?: string;
  lastLogin?: string | null;
}

export type EventType = 'play' | 'skip' | 'star' | 'unstar' | 'rate' | 'thumb_up' | 'thumb_down' | 'thumb_clear';
export type EventSource = 'mix' | 'library' | 'favourites' | 'more_like' | 'search' | null;

export interface EventContext {
  // Listening details
  seeks?: { fromS: number; toS: number }[];
  volumeChanges?: { atS: number; volume: number }[];
  repeatOfPrevious?: boolean;
  addedFrom?: string;
  sessionId?: string;
  sessionPosition?: number;
  // Audio output
  output?: string;
  // Device & network
  deviceKind?: DeviceKind;
  network?: 'wifi' | 'cellular' | 'ethernet' | 'unknown';
  charging?: boolean;
  batteryPct?: number;
  screenOn?: boolean;
  // Search history
  searchQuery?: string;
  // Time & calendar
  localHour?: number;
  weekday?: number;
  holiday?: string | null;
}

export interface PlayEvent {
  id: string;
  type: EventType;
  trackId: string;
  at: string;
  positionS?: number | null;
  durationS?: number | null;
  source?: EventSource;
  mixId?: string | null;
  rating?: number | null;
  offline?: boolean;
  context?: EventContext;
}

// ---- Connect (§5) ----
export type RepeatMode = 'off' | 'all' | 'one';

export interface PlaybackState {
  activeDeviceId: string | null;
  queueId: string | null;
  index: number;
  trackId: string | null;
  positionMs: number;
  positionAt: string;
  playing: boolean;
  volume: number;
  shuffle: boolean;
  repeat: RepeatMode;
  speed: number;
  rev: number;
}

export interface ConnectDevice {
  id: string;
  name: string;
  kind: DeviceKind;
  canPlay: boolean;
  online: boolean;
  isActive: boolean;
}

export type CommandAction =
  | 'play'
  | 'pause'
  | 'next'
  | 'prev'
  | 'seek'
  | 'setVolume'
  | 'setShuffle'
  | 'setRepeat'
  | 'playQueue'
  | 'playTracks'
  | 'setSpeed';

export type ClientMessage =
  | { type: 'hello'; deviceName: string; kind: DeviceKind; canPlay: boolean; appVersion: string }
  | ({ type: 'state' } & PlaybackState)
  | { type: 'command'; action: CommandAction; args?: Record<string, unknown>; targetDeviceId?: string }
  | { type: 'transfer'; toDeviceId: string; play: boolean }
  | { type: 'ping' };

export type ServerMessage =
  | { type: 'welcome'; deviceId: string; devices: ConnectDevice[]; state: PlaybackState | null }
  | { type: 'devices'; devices: ConnectDevice[] }
  | ({ type: 'state' } & PlaybackState)
  | { type: 'state'; state: PlaybackState }
  | { type: 'command'; action: CommandAction; args?: Record<string, unknown>; fromDeviceId?: string }
  | { type: 'takeover'; state: PlaybackState }
  | { type: 'release' }
  | { type: 'queues'; queueId: string }
  | { type: 'pong' };
