<script lang="ts">
  // Admin tag editor (v5). The music files are read-only: edits are stored on the server as overrides on
  // top of the file's tags, survive rescans and can be reverted. Single track, bulk (multi-select) or album.
  import { History, ImageUp, RotateCcw, Search, Tags } from '@lucide/svelte';
  import { tagsApi, type MbRelease, type TagEditLogEntry, type TagSuggestion, type TagValues, type TrackTagDetail } from '../lib/api/admin';
  import { library } from '../lib/library.svelte';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';
  import Sheet from './Sheet.svelte';

  let { trackIds, albumId, title }: { trackIds: string[]; albumId?: string; title: string } = $props();

  const T = $derived(tagsApi(session.api));
  const single = $derived(trackIds.length === 1 && !albumId);
  type Key = 'title' | 'artist' | 'albumArtist' | 'album' | 'trackNo' | 'discNo' | 'year' | 'genre';
  const ALL: { k: Key; label: string; num?: boolean; perTrack?: boolean }[] = [
    { k: 'title', label: 'Title', perTrack: true },
    { k: 'artist', label: 'Artist' },
    { k: 'albumArtist', label: 'Album artist' },
    { k: 'album', label: 'Album' },
    { k: 'trackNo', label: 'Track number', num: true, perTrack: true },
    { k: 'discNo', label: 'Disc number', num: true },
    { k: 'year', label: 'Year', num: true },
    { k: 'genre', label: 'Genre' },
  ];
  const fields = $derived(single ? ALL : ALL.filter((f) => !f.perTrack));

  let details = $state<TrackTagDetail[] | null>(null);
  let error = $state<string | null>(null);
  let values = $state<Record<string, string>>({});
  let initial = $state<Record<string, string>>({});
  let lyrics = $state('');
  let lyricsInitial = $state('');
  let busy = $state(false);
  let history = $state<TagEditLogEntry[] | null>(null);
  let showHistory = $state(false);
  let mb = $state<TagSuggestion[] | null>(null);
  let mbBusy = $state(false);
  let release = $state<MbRelease | null>(null);

  const str = (v: unknown) => (v === null || v === undefined ? '' : String(v));

  async function load() {
    error = null;
    try {
      const ids = trackIds.slice(0, 500);
      const out: TrackTagDetail[] = [];
      for (let i = 0; i < ids.length; i += 20) out.push(...(await Promise.all(ids.slice(i, i + 20).map((id) => T.track(id)))));
      details = out;
      const v: Record<string, string> = {};
      for (const f of ALL) {
        const vals = new Set(out.map((d) => str(d.effective[f.k])));
        v[f.k] = vals.size === 1 ? [...vals][0] : '';
      }
      values = { ...v };
      initial = { ...v };
      lyrics = lyricsInitial = single ? (out[0]?.lyrics ?? '') : '';
    } catch (e) {
      error = (e as Error).message;
    }
  }
  $effect(() => {
    void trackIds;
    void load();
  });

  const mixed = (k: Key) => !!details && new Set(details.map((d) => str(d.effective[k]))).size > 1;
  const overridden = (k: string) => !!details?.some((d) => d.overridden.includes(k as never));
  const fileValue = (k: Key) => (single && details?.[0] ? str(details[0].file[k]) : '');

  function changes(): TagValues {
    const set: Record<string, unknown> = {};
    for (const f of fields) {
      if (values[f.k] === initial[f.k]) continue;
      const raw = values[f.k].trim();
      if (f.num) {
        if (raw && !/^\d+$/.test(raw)) throw new Error(`${f.label} must be a number`);
        set[f.k] = raw ? Number(raw) : null;
      } else set[f.k] = raw || null;
    }
    if (single && lyrics !== lyricsInitial) set.lyrics = lyrics.trim() ? lyrics : null;
    return set as TagValues;
  }

  async function done(msg: string, newAlbum?: string) {
    ui.toast(msg);
    library.tagsChanged(albumId ?? details?.[0]?.albumId ?? null, newAlbum ?? null);
    await load();
    if (showHistory) await loadHistory();
  }

  async function save() {
    let set: TagValues;
    try {
      set = changes();
    } catch (e) {
      return ui.toast((e as Error).message);
    }
    if (!Object.keys(set).length) return ui.toast('Nothing changed');
    busy = true;
    try {
      const r = albumId ? await T.editAlbum(albumId, set) : await T.edit(trackIds, set);
      const now = await T.track(trackIds[0]);
      await done(`Saved (${r.changed} change${r.changed === 1 ? '' : 's'})`, now.albumId);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function revert(fieldsToRevert: string[], label: string) {
    if (!(await ui.confirm('Revert to file tags', `${label} goes back to what the music file says.`, 'Revert'))) return;
    busy = true;
    try {
      const r = albumId ? await T.editAlbum(albumId, {}, fieldsToRevert) : await T.edit(trackIds, {}, fieldsToRevert);
      await done(`Reverted (${r.changed})`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function uploadCover(ev: Event) {
    const file = (ev.target as HTMLInputElement).files?.[0];
    if (!file || !albumId) return;
    busy = true;
    try {
      const r = await T.uploadCover(albumId, file);
      session.api.forgetArt(albumId);
      if (r.albumId) session.api.forgetArt(r.albumId);
      await done('Cover updated', r.albumId);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function revertCover() {
    if (!albumId) return;
    busy = true;
    try {
      await T.revertCover(albumId);
      session.api.forgetArt(albumId);
      await done('Cover reverted');
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function lookup() {
    mbBusy = true;
    mb = null;
    release = null;
    try {
      const r = albumId ? await T.musicbrainz({ albumId }) : await T.musicbrainz({ trackId: trackIds[0] });
      mb = r.results;
      if (!mb.length) ui.toast('No matches on MusicBrainz');
    } catch (e) {
      ui.toast((e as Error).message);
    }
    mbBusy = false;
  }

  async function apply(s: TagSuggestion) {
    const put = (k: Key, v: unknown) => {
      if (v !== null && v !== undefined && fields.some((f) => f.k === k)) values[k] = String(v);
    };
    if (albumId && s.releaseMbid) {
      mbBusy = true;
      try {
        release = await T.mbRelease(s.releaseMbid);
        put('album', release.album);
        put('albumArtist', release.albumArtist);
        put('year', release.year);
        put('genre', release.genre);
      } catch (e) {
        ui.toast((e as Error).message);
      }
      mbBusy = false;
    } else {
      put('title', s.title);
      put('artist', s.artist);
      put('album', s.album);
      put('albumArtist', s.albumArtist);
      put('year', s.year);
      put('trackNo', s.trackNo);
      put('discNo', s.discNo);
    }
    mb = null;
  }

  /** Album + MusicBrainz release: also set each track's title by disc/track number. */
  async function applyTrackTitles() {
    if (!release || !details) return;
    busy = true;
    let n = 0;
    try {
      for (const d of details) {
        const disc = d.effective.discNo ?? 1;
        const m = release.tracks.find((t) => t.trackNo === d.effective.trackNo && (t.discNo ?? 1) === disc);
        if (m && m.title !== d.effective.title) {
          await T.edit([d.trackId], { title: m.title });
          n++;
        }
      }
      await done(`${n} track title${n === 1 ? '' : 's'} updated`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function loadHistory() {
    try {
      history = await T.history(albumId ? { albumId, limit: 200 } : single ? { trackId: trackIds[0], limit: 200 } : { limit: 200 });
      if (!albumId && !single) history = history.filter((h) => trackIds.includes(h.trackId));
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  const fmtV = (v: unknown) => (v === null || v === undefined || v === '' ? '∅' : String(v));
</script>

<Sheet label="Edit tags" wide>
  <div class="pad">
    <h2 class="title"><Tags size={26} /> Edit tags</h2>
    <p class="muted small">{title} · {albumId ? 'whole album' : trackIds.length === 1 ? '1 track' : `${trackIds.length} tracks`}. The music files are never changed; edits are kept on the server and survive rescans.</p>
    {#if error}<p class="error">{error}</p>
    {:else if !details}<p class="muted">Loading…</p>
    {:else}
      {#if single}<p class="muted small path">{details[0].path}</p>{/if}
      <div class="grid">
        {#each fields as f (f.k)}
          <label class="fl">
            <span>{f.label}{#if overridden(f.k)}<span class="dot" title="Edited (differs from the file)">●</span>{/if}</span>
            <input
              type="text"
              inputmode={f.num ? 'numeric' : undefined}
              bind:value={values[f.k]}
              placeholder={mixed(f.k) ? '(several values — leave empty to keep)' : fileValue(f.k) ? `file: ${fileValue(f.k)}` : ''}
            />
          </label>
          {#if single && overridden(f.k)}
            <button class="btn text tiny" title="Back to the file's value: {fileValue(f.k) || '∅'}" onclick={() => revert([f.k], f.label)} disabled={busy}><RotateCcw size={14} /></button>
          {:else}<span></span>{/if}
        {/each}
      </div>
      {#if !single}<p class="muted small">Only fields you change are written. Clearing a field you changed clears that tag on every track.</p>{/if}
      {#if single}
        <label class="fl lyr">
          <span>Lyrics for everyone (LRC or plain text){#if overridden('lyrics')}<span class="dot">●</span>{/if}</span>
          <textarea rows="5" bind:value={lyrics} placeholder="Leave empty to use the file's lyrics"></textarea>
        </label>
      {/if}
      {#if albumId}
        <div class="row">
          <label class="btn"><ImageUp size={16} /> Upload cover<input type="file" accept="image/jpeg,image/png,image/webp" hidden onchange={uploadCover} /></label>
          {#if overridden('cover')}<button class="btn text" onclick={revertCover} disabled={busy}>Use the file's cover</button>{/if}
        </div>
      {/if}

      <div class="row">
        <button class="btn" onclick={lookup} disabled={mbBusy || busy}><Search size={16} /> {mbBusy ? 'Asking MusicBrainz…' : 'Suggest from MusicBrainz'}</button>
        <button class="btn text" onclick={() => revert(['*'], albumId ? 'Every tag of every track in this album' : 'Every tag')} disabled={busy || !details.some((d) => d.overridden.length)}><RotateCcw size={16} /> Revert to file tags</button>
        <button class="btn text" onclick={() => { showHistory = !showHistory; if (showHistory) void loadHistory(); }}><History size={16} /> History</button>
      </div>
      {#if mb}
        <div class="mb">
          {#each mb as s, i (i)}
            <button class="row-item sugg" onclick={() => apply(s)}>
              <div>
                <b>{albumId ? s.album : s.title}</b> — {albumId ? s.albumArtist : s.artist}
                <div class="muted small">{[s.album && !albumId ? s.album : null, s.year, s.trackNo ? `#${s.trackNo}` : null, s.country, s.format, s.trackCount ? `${s.trackCount} tracks` : null].filter(Boolean).join(' · ')} {s.score != null ? `· ${s.score}%` : ''}</div>
              </div>
            </button>
          {/each}
          <p class="muted small">Picking a suggestion fills the form; nothing is saved until you press Save.</p>
        </div>
      {/if}
      {#if release}
        <div class="row"><button class="btn text" onclick={applyTrackTitles} disabled={busy}>Also set track titles from “{release.album}” ({release.tracks.length} tracks)</button></div>
      {/if}
      {#if showHistory}
        <div class="hist">
          {#if !history}<p class="muted">Loading…</p>
          {:else if !history.length}<p class="muted">No edits yet.</p>
          {:else}
            {#each history as h (h.id)}
              <div class="h small"><span class="muted">{new Date(h.at).toLocaleString()} · {h.user ?? '?'}</span> {#if !single}<b>{h.trackTitle}</b>{/if} {h.action === 'revert' ? 'reverted' : 'set'} <b>{h.field}</b>: {fmtV(h.oldValue)} → {fmtV(h.newValue)}</div>
            {/each}
          {/if}
        </div>
      {/if}
    {/if}
  </div>
  <div class="foot">
    <button class="btn text" onclick={() => ui.close()}>Close</button>
    <button class="btn" onclick={save} disabled={busy || !details}>{busy ? 'Saving…' : 'Save'}</button>
  </div>
</Sheet>

<style>
  .pad {
    padding: 16px 20px;
    max-height: 75vh;
    overflow: auto;
  }
  .title {
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .path {
    word-break: break-all;
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 8px 6px;
    align-items: end;
  }
  .fl {
    display: grid;
    gap: 4px;
  }
  .fl span {
    font-size: 0.8rem;
    color: var(--fg-2);
  }
  .fl input,
  .fl textarea {
    width: 100%;
    box-sizing: border-box;
  }
  .lyr {
    margin-top: 12px;
  }
  .dot {
    color: var(--accent);
    margin-left: 6px;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
    align-items: center;
  }
  .tiny {
    padding: 4px;
  }
  .sugg {
    text-align: left;
    width: 100%;
  }
  .mb,
  .hist {
    margin-top: 10px;
    border-top: 1px solid var(--line, #3333);
    padding-top: 8px;
  }
  .h {
    padding: 3px 0;
  }
  .foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 10px 16px;
  }
</style>
