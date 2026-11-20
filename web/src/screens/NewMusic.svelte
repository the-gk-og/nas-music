<script lang="ts">
  // v7: "New this week" (GET /library/new) with the notification bell list on top.
  import { ArrowLeft, Bell } from '@lucide/svelte';
  import ItemGrid from '../components/ItemGrid.svelte';
  import TrackList from '../components/TrackList.svelte';
  import { notifications } from '../lib/notifications.svelte';
  import { session } from '../lib/session.svelte';
  import type { NewMusic, ShelfItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let data = $state<NewMusic | null>(null);
  let error = $state<string | null>(null);
  let wantsNotes = $state<boolean | null>(null);
  $effect(() => {
    session.api
      .newMusic(7)
      .then((d) => (data = d))
      .catch((e) => (error = e.status === 404 ? 'This server does not list new music yet.' : e.message));
    session.api
      .getSettings()
      .then((s) => (wantsNotes = s.settings['notify.newMusic'] === true))
      .catch(() => (wantsNotes = null));
    void notifications.load().then(() => notifications.markAllRead());
  });
  const albums = $derived<ShelfItem[]>(
    (data?.albums ?? []).map((x) => ({ type: 'album', id: x.id, title: x.title, subtitle: `${x.artist}${x.year ? ` · ${x.year}` : ''}`, albumId: x.hasArt === false ? null : x.id }) as ShelfItem),
  );
  const when = (s: string) => new Date(s).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

  async function turnOn() {
    try {
      await session.api.putSettings({ settings: { 'notify.newMusic': true } });
      wantsNotes = true;
      ui.toast("You'll be told when new music is added");
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>New this week</h1>
  </div>

  {#if wantsNotes === false}
    <div class="hint pad">
      <Bell size={18} /><span>Get a notification when new music is added to the library?</span>
      <button class="btn" onclick={turnOn}>Turn on</button>
    </div>
  {/if}

  {#if notifications.items.length}
    <section>
      <h2>Notifications</h2>
      {#each notifications.items.slice(0, 10) as n (n.id)}
        <div class="note" class:unread={!n.read}>
          <div class="t">{n.title}</div>
          <div class="small muted">{n.body}</div>
          <div class="small muted">{when(n.createdAt)}</div>
        </div>
      {/each}
    </section>
  {/if}

  {#if error}<div class="empty">{error}</div>
  {:else if !data}<div class="empty">Loading…</div>
  {:else}
    {#if albums.length}
      <section>
        <h2>New albums</h2>
        <ItemGrid items={albums} section="new.albums" />
      </section>
    {/if}
    {#if data.tracks.length}
      <section>
        <h2>New songs</h2>
        <TrackList tracks={data.tracks} queueName="New this week" />
      </section>
    {/if}
    {#if !albums.length && !data.tracks.length}<div class="empty">Nothing new in the last {data.days} days.</div>{/if}
  {/if}
</div>

<style>
  section {
    margin-bottom: 24px;
  }
  h2 {
    padding: 0 16px;
    font-size: 1.3rem;
  }
  .hint {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin: 0 16px 16px;
    padding: 12px;
    border-radius: 8px;
    background: var(--bg-2);
  }
  .hint span {
    flex: 1;
    min-width: 180px;
  }
  .note {
    padding: 8px 16px;
    border-left: 3px solid transparent;
  }
  .note.unread {
    border-left-color: var(--accent);
  }
  .t {
    font-weight: 600;
  }
</style>
