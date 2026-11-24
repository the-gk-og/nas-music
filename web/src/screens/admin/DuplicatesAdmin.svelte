<script lang="ts">
  // Admin: duplicate finder (v6). Copies of the same recording found by audio fingerprint. Pick the copy to
  // keep; the others are hidden from the library, or (v10) moved to the trash to be deleted. Each copy can be
  // played; switching copies keeps the position so you can compare them by ear.
  import { Eye, EyeOff, Pause, Play, RefreshCw, Trash2 } from '@lucide/svelte';
  import { autotagApi, cleanupApi, losslessLabel, qualityText, type DuplicateGroup } from '../../lib/api/admin';
  import { fmtBytes } from '../../lib/format';
  import { library } from '../../lib/library.svelte';
  import { preview } from '../../lib/preview.svelte';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const T = $derived(autotagApi(session.api));
  const C = $derived(cleanupApi(session.api));
  let fmt = $state<'original' | 'opus'>('original');
  $effect(() => () => preview.stop());

  function listen(g: DuplicateGroup, id: string) {
    // Same song, another copy: keep the position (A/B). Another song: start from the top.
    const sameGroup = g.tracks.some((t) => t.id === preview.trackId);
    preview.toggle(id, C.previewUrl(id, fmt), sameGroup);
  }
  let groups = $state<DuplicateGroup[] | null>(null);
  let keep = $state<Record<string, string>>({});
  let busy = $state(false);
  let showResolved = $state(false);

  async function load() {
    try {
      const gs = await T.duplicates();
      groups = gs;
      keep = Object.fromEntries(gs.map((g) => [g.groupId, (g.tracks.find((t) => !t.hidden && g.hiddenCount) ?? g.tracks.find((t) => t.recommended) ?? g.tracks[0]).id]));
    } catch (e) {
      ui.toast((e as Error).message);
      groups = [];
    }
  }
  $effect(() => {
    void load();
  });

  const resolved = (g: DuplicateGroup) => g.hiddenCount === g.tracks.length - 1;
  const visible = $derived((groups ?? []).filter((g) => showResolved || !resolved(g)));

  async function resolve(g: DuplicateGroup, remove = false) {
    const k = keep[g.groupId];
    const hide = g.tracks.filter((t) => t.id !== k).map((t) => t.id);
    const kt = g.tracks.find((t) => t.id === k)!;
    const n = `${hide.length} other cop${hide.length === 1 ? 'y' : 'ies'}`;
    const ok = remove
      ? await ui.confirm(
          'Delete duplicates',
          `Keep "${kt.path}" and move ${n} to the trash? Playlists and favourites move to the kept copy. The trash is emptied after 30 days (Cleanup → Trash); until then you can restore them.`,
          'Move to trash',
        )
      : await ui.confirm(
          'Hide duplicates',
          `Keep "${kt.path}" and hide ${n} from the library? Playlists and favourites move to the kept copy. No file is deleted; you can show them again here.`,
          'Hide',
        );
    if (!ok) return;
    busy = true;
    try {
      if (hide.includes(preview.trackId ?? '')) preview.stop();
      await T.resolve(k, hide, remove);
      ui.toast(remove ? `Moved ${n} to the trash` : `Hid ${n}`);
      library.tagsChanged(null, null);
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function unhide(ids: string[]) {
    busy = true;
    try {
      const r = await T.unhide(ids);
      ui.toast(`${r.unhidden} shown again`);
      library.tagsChanged(null, null);
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }
  const dur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
</script>

<h2>Duplicates</h2>
<p class="muted small">
  Copies of the same recording (e.g. “Song (2).flac”, or a FLAC and an MP3), found by comparing audio fingerprints from the tag-fixing job. Choose the
  copy to keep; hide the others, or move them to the trash to be deleted. Press play on each copy to compare them by ear: switching copies keeps
  the position.
</p>
<div class="row">
  <label class="row small"><input type="checkbox" bind:checked={showResolved} /> Show resolved groups</label>
  <label class="row small" title="Original plays the file as stored (what you'd keep); Opus works for every format">
    Listen as
    <select bind:value={fmt} onchange={() => preview.stop()}>
      <option value="original">original file</option>
      <option value="opus">Opus (any format)</option>
    </select>
  </label>
  <button class="btn sm text" onclick={load}><RefreshCw size={15} /> Refresh</button>
</div>

{#if preview.error}<p class="small bad">{preview.error}</p>{/if}

{#if !groups}<p class="muted">Looking for duplicates…</p>
{:else if !visible.length}<p class="muted">{groups.length ? 'All duplicate groups are resolved.' : 'No duplicates found (songs need fingerprints first: Tag fixing → Run now).'}</p>
{:else}
  <p class="muted small">{visible.length} group{visible.length === 1 ? '' : 's'}</p>
  {#each visible as g (g.groupId)}
    <article class="group">
      <div class="row head">
        <b class="grow">{g.tracks[0].title} <span class="muted">— {g.tracks[0].artist}</span></b>
        {#if g.similarity != null}<span class="small muted">{Math.round(g.similarity * 100)}% alike</span>{/if}
      </div>
      {#each g.tracks as t (t.id)}
        {@const ll = losslessLabel(t.losslessVerdict)}
        <label class="copy" class:hidden={t.hidden}>
          <input type="radio" name="keep-{g.groupId}" checked={keep[g.groupId] === t.id} onchange={() => (keep = { ...keep, [g.groupId]: t.id })} />
          <button
            class="icon-btn play"
            class:on={preview.trackId === t.id && preview.playing}
            aria-label={preview.trackId === t.id && preview.playing ? 'Pause' : 'Listen to this copy'}
            title="Listen (switching copies keeps the position)"
            onclick={(e) => {
              e.preventDefault();
              listen(g, t.id);
            }}
          >
            {#if preview.trackId === t.id && preview.playing}<Pause size={16} />{:else}<Play size={16} />{/if}
          </button>
          <div class="grow">
            <div class="mono small path" title={t.path}>{t.path}</div>
            <div class="small muted">
              {qualityText(t)} · {dur(t.durationS)} · {fmtBytes(t.bytes)} · {t.album}
              {#if t.recommended}<span class="ok"> · suggested keep</span>{/if}
              {#if ll && t.losslessVerdict !== 'ok'}<span class="bad" title={t.losslessNote ?? ''}> · {ll}</span>{/if}
              {#if t.hidden}<span class="warnc"> · hidden</span>{/if}
            </div>
          </div>
          {#if t.hidden}<button class="btn sm text" disabled={busy} onclick={(e) => { e.preventDefault(); void unhide([t.id]); }}><Eye size={14} /> Show</button>{/if}
        </label>
      {/each}
      <div class="row acts">
        <button class="btn sm primary" disabled={busy} onclick={() => resolve(g)}><EyeOff size={15} /> Keep selected, hide others</button>
        <button class="btn sm danger" disabled={busy} onclick={() => resolve(g, true)}><Trash2 size={15} /> Keep selected, delete others</button>
      </div>
    </article>
  {/each}
{/if}

<style>
  .row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }
  .group {
    background: var(--bg-1);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 12px 14px;
    margin: 12px 0;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .copy {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 8px 0;
    border-top: 1px solid var(--line);
    cursor: pointer;
  }
  .copy.hidden .path {
    text-decoration: line-through;
    opacity: 0.7;
  }
  .path {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .acts {
    margin-top: 8px;
  }
  .play {
    flex: none;
  }
  .play.on {
    color: var(--accent);
  }
</style>
