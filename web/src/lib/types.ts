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
  /** v6: contains explicit tracks (of those the caller can see) — "E" badge. */
  explicit?: boolean;
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
  /** Volume levelling (ReplayGain 2.0 scale); absent until the server has measured the track. */
  replayGain?: ReplayGain | null;
  /** v6 explicit-content flag; "probable" = lyrics word list, not certain. */
  explicit?: ExplicitStatus;
  /** v6, queues/playlists only: hidden by the caller's family mode — players skip it. */
  familyHidden?: boolean;
}

export type ExplicitStatus = 'explicit' | 'probable' | 'clean' | 'unknown';

/** v7 voice control (Home Assistant Assist). The token itself is only in the create response. */
export interface VoiceToken {
  id: string;
  name: string;
  createdAt: string;
  lastUsed: string | null;
  token?: string;
}

export interface VoiceDevice {
  id: string;
  name: string;
  kind: string;
  castType?: string;
  online: boolean;
  aliases: string[];
  isDefault: boolean;
}

export interface VoiceResult {
  ok: boolean;
  intent: string;
  speech: string;
  device?: { id: string; name: string };
  item?: { kind: string; id: string | null; title: string };
  queueId?: string;
}

export interface FamilyMode {
  enabled: boolean;
  hideProbable: boolean;
  hideUnknown: boolean;
  /** Locked by an admin: the user can only tighten it (loosening needs the PIN). */
  locked: boolean;
  hasPin: boolean;
}

export interface ReplayGain {
  trackGain: number;
  trackPeak?: number | null;
  albumGain?: number | null;
  albumPeak?: number | null;
}

export interface AutoEqEntry {
  id: string;
  name: string;
  source: string;
  rig?: string | null;
  form: string;
}

export interface AutoEqFilter {
  type: 'PK' | 'LSC' | 'HSC';
  fc: number;
  gainDb: number;
  q: number;
}

export interface AutoEqProfile extends AutoEqEntry {
  preampDb: number;
  filters: AutoEqFilter[];
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

export type PlaylistRole = 'owner' | 'editor' | 'viewer';

export interface PlaylistEntry {
  id: string;
  trackId: string;
  addedAt: string;
  addedById?: string | null;
  addedBy?: string | null;
}

export interface Playlist {
  id: string;
  name: string;
  trackCount?: number;
  durationS?: number;
  createdAt?: string;
  updatedAt?: string;
  tracks?: Track[];
  // v5 (collaborative playlists)
  ownerId?: string;
  owner?: string | null;
  role?: PlaylistRole;
  rev?: number;
  memberCount?: number;
  collaborative?: boolean;
  /** Parallel to `tracks`. */
  entries?: PlaylistEntry[];
}

export interface PlaylistMember {
  userId: string;
  username: string;
  role: PlaylistRole;
  addedAt?: string;
}

export interface PlaylistActivity {
  id: number;
  at: string;
  userId?: string | null;
  user?: string | null;
  action: 'create' | 'rename' | 'add' | 'remove' | 'move' | 'member_add' | 'member_role' | 'member_remove' | 'leave';
  trackId?: string | null;
  trackTitle?: string | null;
  detail: Record<string, unknown>;
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
  /** The active phone is casting with Google Cast to this speaker (name); null/absent = not casting. */
  castTo?: string | null;
  /** Set by the server: the active device is a speaker still connecting/loading (`playing` is false). */
  connecting?: boolean;
  rev: number;
}

/** Server-side cast target types (api/openapi.yaml ConnectDevice.castType). */
export type CastType = 'chromecast' | 'dlna' | 'homeassistant' | 'snapcast';

export interface ConnectDevice {
  id: string;
  name: string;
  /** A DeviceKind, or 'speaker' for a speaker the server plays on (Chromecast, DLNA, Home Assistant, Snapcast). */
  kind: DeviceKind | 'speaker';
  canPlay: boolean;
  online: boolean;
  isActive: boolean;
  castType?: CastType | null;
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
  | { type: 'playlist'; playlistId: string; rev: number | null; deleted?: boolean }
  | { type: 'family'; family: FamilyMode }
  | { type: 'pong' }
  | { type: 'error'; detail: string; source?: 'speaker' };

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
  | 'smart_playlist'
  | 'genre'
  | 'folder'
  | 'blend'
  | 'wrapped';

export interface RadioSeed {
  type: 'random' | 'track' | 'artist' | 'album' | 'playlist' | 'smart_playlist' | 'moment' | 'vibe';
  id?: string | null;
  title?: string | null;
  /** vibe: what the user typed / said */
  text?: string | null;
  /** vibe: interpreted chips (from POST /radio/vibe/parse, possibly with some removed) */
  constraints?: VibeConstraint[] | null;
  /** responses: why these picks */
  explanation?: string | null;
  /** responses: short steering summary */
  steering?: string | null;
}

/** AI radio (v6): one interpreted describe-a-vibe chip. Clients show `label` and may drop chips. */
export interface VibeConstraint {
  id: string;
  kind: 'energy' | 'tempo' | 'danceability' | 'year' | 'vocals' | 'mood' | 'genre' | 'familiarity' | 'like';
  label: string;
  min?: number;
  max?: number;
  value?: string;
  weight?: number;
  exclude?: boolean;
  name?: string;
}

export interface VibeParse {
  text: string;
  title: string;
  constraints: VibeConstraint[];
}

export interface RadioContext {
  localHour?: number;
  weekday?: number;
  deviceKind?: 'phone' | 'car' | 'speaker' | 'desktop' | 'watch' | 'web' | 'tv';
  output?: string;
}

export type SteerAction =
  | 'more_like'
  | 'less_like'
  | 'more_energy'
  | 'calmer'
  | 'more_variety'
  | 'more_familiar'
  | 'never_here'
  | 'remove'
  | 'reset';

export interface RadioSteerRef {
  type: 'track' | 'artist';
  id: string;
  name?: string | null;
}

export interface RadioStation {
  queueId?: string;
  radioId: string;
  kind: RadioSeed['type'];
  title: string;
  stationKey: string;
  explanation?: string | null;
  text?: string | null;
  constraints?: VibeConstraint[] | null;
  steeringSummary?: string | null;
  steering: { energy: number; variety: number; moreLike: RadioSteerRef[]; lessLike: RadioSteerRef[]; never: RadioSteerRef[] };
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
  smartPlaylist?: SmartPlaylist;
  device?: { kind?: ConnectDevice['kind']; canPlay?: boolean; isActive?: boolean; playing?: boolean; nowPlaying?: Track | null };
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

export type PinType = 'album' | 'artist' | 'playlist' | 'mix' | 'queue' | 'smart_list' | 'smart_playlist' | 'genre' | 'folder';

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

// ---------- v7: smart playlists ----------

export type SmartFieldType = 'number' | 'percent' | 'text' | 'date' | 'bool' | 'enum' | 'playlist';
export type SmartUnit = 'days' | 'weeks' | 'months' | 'years';

export interface SmartRule {
  field: string;
  op: string;
  value?: number | string | boolean | [number, number] | null;
  unit?: SmartUnit;
}

export interface SmartGroup {
  match: 'all' | 'any';
  rules: (SmartRule | SmartGroup)[];
}

export interface SmartDefinition extends SmartGroup {
  sort: string;
  limit: { type: 'songs' | 'minutes' | 'none'; value?: number };
}

export interface SmartPlaylist {
  id: string;
  name: string;
  definition: SmartDefinition;
  preset?: string | null;
  createdAt?: string;
  updatedAt?: string;
  refreshedAt?: string | null;
  trackCount: number;
  durationS: number;
  rev: number;
  tracks?: Track[];
}

export interface SmartFieldSpec {
  id: string;
  label: string;
  type: SmartFieldType;
  ops: string[];
  options?: string[];
  suggest?: 'genres' | 'artists';
  unit?: string;
}

export interface SmartPreset {
  id: string;
  name: string;
  description?: string;
  definition: SmartDefinition;
}

export interface SmartFields {
  fields: SmartFieldSpec[];
  sorts: { id: string; label: string }[];
  units: SmartUnit[];
  presets: SmartPreset[];
}

export interface SmartPreview {
  trackCount: number;
  durationS: number;
  tracks: Track[];
}

// ---------- v7: artist pages ----------

export interface ArtistLink {
  kind: string;
  url: string;
}

export interface ArtistInfo {
  status: 'ok' | 'not_found' | 'error' | 'pending' | 'none';
  fetchedAt?: string;
  mbid?: string | null;
  type?: string | null;
  disambiguation?: string | null;
  country?: string | null;
  area?: string | null;
  beginArea?: string | null;
  lifeSpan?: { begin?: string | null; end?: string | null; ended?: boolean };
  yearsActive?: { from?: number | null; to?: number | null };
  genres?: string[];
  links?: ArtistLink[];
  bio?: { text: string; source: string; url: string; license: string; licenseUrl: string } | null;
  image?: { source: string; author?: string | null; license?: string | null; licenseUrl?: string | null; filePage?: string | null; credit?: string | null } | null;
  photoUrl?: string | null;
}

export interface ArtistPage {
  id: string;
  name: string;
  mbid?: string | null;
  artUrl?: string | null;
  photoUrl?: string | null;
  info: ArtistInfo;
  stats: { trackCount: number; durationS: number; playCount: number; albumCount: number; singleCount: number };
  topTracks: (Track & { playCount?: number })[];
  albums: Album[];
  singles: Album[];
  appearsOn: Album[];
  similar: (Artist & { similarity?: number; artUrl?: string | null })[];
  radioSeed: RadioSeed;
}
