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

export type LyricsSource = 'embedded' | 'lrc_file' | 'user' | 'lrclib';

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
  /** Sources available for this track (server v2+). */
  sources?: LyricsSource[];
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
  id: string;
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
  current?: boolean;
  appVersion?: string | null;
}

export interface DataSummary {
  events: { count: number; withContext: number; first: string | null; last: string | null };
  signals: Record<string, { enabled?: boolean; fields?: string[]; count: number; first?: string | null; last?: string | null }>;
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
  /** v3: set on radio queues; the client tops them up via POST /radio/{id}/more. */
  radio?: RadioSeed | null;
  source?: { type: string; id?: string | null } | null;
}

export interface Invite {
  code: string;
  url?: string;
  note?: string | null;
  createdAt?: string;
  expiresAt: string;
  usedBy?: string | null;
  usedAt?: string | null;
  createdBy?: string;
}

export interface AdminUser {
  id: string;
  username: string;
  isAdmin: boolean;
  disabled: boolean;
  createdAt?: string;
  lastLogin?: string | null;
  deviceCount?: number;
}

export type EventType = 'play' | 'skip' | 'star' | 'unstar' | 'rate' | 'thumb_up' | 'thumb_down' | 'thumb_clear';
export type EventSource = 'mix' | 'library' | 'favourites' | 'more_like' | 'search' | null;

export interface EventContext {
  // Listening details
  seeks?: { fromMs: number; toMs: number }[];
  volumeChanges?: { atMs: number; volume: number }[];
  repeatOfPrevious?: boolean;
  addedFrom?: string;
  sessionId?: string;
  sessionPosition?: number;
  // Audio output
  output?: string;
  // Device & network
  deviceKind?: DeviceKind;
  network?: 'wifi' | 'cellular' | 'ethernet' | 'none';
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
  | { type: 'pong' }
  | { type: 'error'; detail: string };

// ---- v3 (docs/v3-ux.md; api/openapi.yaml v3 from server core) ----

export type ItemType =
  | 'track'
  | 'album'
  | 'artist'
  | 'playlist'
  | 'mix'
  | 'radio'
  | 'queue'
  | 'device'
  | 'catalog'
  | 'suggestion'
  | 'toget'
  | 'smart_list'
  | 'genre'
  | 'folder';

export interface RadioSeed {
  type: 'random' | 'track' | 'artist' | 'album' | 'playlist';
  id?: string | null;
  title?: string | null;
}

/** Generic card. `artUrl` is relative to the API base, e.g. "art/<albumId>" or "catalog/<id>/cover". */
export interface ShelfItem {
  type: ItemType;
  id: string;
  title: string;
  subtitle?: string | null;
  artUrl?: string | null;
  track?: Track;
  album?: Album;
  artist?: Artist;
  playlist?: Playlist;
  queue?: Queue;
  mix?: { kind?: string; trackCount?: number; durationS?: number };
  seed?: RadioSeed;
  device?: { kind?: DeviceKind; canPlay?: boolean; isActive?: boolean; playing?: boolean; nowPlaying?: Track | null };
  progress?: { index?: number; count?: number; positionMs?: number; queueId?: string | null };
  catalog?: CatalogItem;
  suggestion?: Suggestion;
  toget?: ToGetItem;
  playedAt?: string | null;
  /** release_radar shelves */
  release?: ReleaseInfo;
  addedAt?: string | null;
  /** blends shelf; status "new" = the "Start a blend" card */
  blend?: { status?: 'joined' | 'invited' | 'new'; matchPct?: number | null; trackCount?: number; members?: string[] };
  wrapped?: { year: number; final: boolean };
  // Client-side art hints (mock / pages): album id or up to four for a collage.
  albumId?: string | null;
  artIds?: string[];
}

export type ShelfKind =
  | 'start_radio'
  | 'devices'
  | 'recently_played'
  | 'continue_listening'
  | 'pinned'
  | 'mixes'
  | 'radio'
  | 'because_you_played'
  | 'release_radar'
  | 'suggestions'
  | 'to_get'
  | 'blends'
  | 'wrapped';

export interface Shelf {
  id: string;
  kind: ShelfKind | string;
  title: string;
  subtitle?: string | null;
  layout?: 'button' | 'row' | 'grid2';
  items: ShelfItem[];
}

export interface Feed {
  shelves: Shelf[];
}

export interface CatalogItem {
  /** Opaque, e.g. "dz:album:302127". */
  id: string;
  type: 'album' | 'track' | 'artist';
  title: string;
  artist?: string | null;
  album?: string | null;
  year?: number | null;
  durationS?: number | null;
  mbid?: string | null;
  artUrl?: string | null;
  hasPreview: boolean;
  owned: boolean;
  ownedId?: string | null;
  toGet?: { id: string; status: string } | null;
  links?: { name: string; url: string }[];
}

export interface SearchResults {
  query?: string;
  top?: ShelfItem | null;
  tracks: Track[];
  albums: Album[];
  artists: Artist[];
  playlists: Playlist[];
  queues?: Queue[];
  catalog: CatalogItem[];
  catalogError?: string | null;
}

export type ToGetStatus = 'want' | 'got' | 'dismissed';

export interface ToGetItem {
  id: string;
  type: 'album' | 'track';
  title: string;
  artist: string;
  year?: number | null;
  mbid?: string | null;
  catalogId?: string | null;
  artUrl?: string | null;
  hasPreview?: boolean;
  note?: string | null;
  status: ToGetStatus;
  source?: 'search' | 'suggestion' | 'manual';
  suggestionId?: string | null;
  ownedId?: string | null;
  createdAt?: string;
  updatedAt?: string;
  gotAt?: string | null;
  links?: { name: string; url: string }[];
}

export type PinType = 'album' | 'artist' | 'playlist' | 'mix' | 'queue' | 'smart_list' | 'genre' | 'folder';

// ---- v5 discovery phase 2: Release Radar, Wrapped, Blend ----

export interface ReleaseInfo {
  date?: string | null;
  /** Album, EP, Single */
  type?: string | null;
  upcoming?: boolean;
  artist?: string | null;
}

export interface ReleaseRadar {
  updatedAt?: string | null;
  windowDays?: number;
  inLibrary: ShelfItem[];
  notOwned: ShelfItem[];
}

export interface WrappedCount {
  plays: number;
  minutes: number;
}

export interface WrappedArtist extends WrappedCount {
  artist: Artist;
  artUrl?: string | null;
}

export interface AudioAura {
  label: string;
  description: string;
  energy: number;
  danceability?: number | null;
  valence?: number | null;
  bpm?: number | null;
  moods: { name: string; score: number }[];
  colors: string[];
}

export interface WrappedBase {
  year: number;
  final: boolean;
  from?: string;
  to?: string;
  timezone?: string;
  totals: { minutes: number; plays: number; skips?: number; tracks: number; artists: number; albums: number; days?: number };
  topTracks: (WrappedCount & { track: Track })[];
  topArtists: WrappedArtist[];
  topAlbums: (WrappedCount & { album: Album })[];
  topGenres: { name: string; minutes: number; share: number }[];
  byHour: number[];
  byWeekday: number[];
  byMonth: number[];
  peakHour?: number | null;
  peakWeekday?: number | null;
  topDay?: { date: string; minutes: number } | null;
  aura?: AudioAura | null;
}

export interface Wrapped extends WrappedBase {
  streaks?: { longest: number; longestFrom?: string | null; longestTo?: string | null; current: number };
  discoveries?: { artists: number; tracks: number; share: number; topArtists: WrappedArtist[] };
  firstPlay?: { track: Track; at: string } | null;
}

export interface ServerWrapped extends WrappedBase {
  listeners: number;
  users: number;
}

export interface BlendMember {
  userId: string;
  username: string;
  status: 'joined' | 'invited';
  joinedAt?: string | null;
}

export interface Blend {
  id: string;
  name: string;
  createdBy?: { userId: string; username: string };
  myStatus: 'joined' | 'invited';
  invitedBy?: string | null;
  members: BlendMember[];
  matchPct?: number | null;
  pairs?: { a: string; b: string; aName?: string; bName?: string; pct?: number | null }[];
  trackCount?: number;
  createdAt?: string;
  updatedAt?: string;
  tracks?: { track: Track; forUsers: string[]; reason: 'shared' | 'top' | 'match' | string }[];
}
