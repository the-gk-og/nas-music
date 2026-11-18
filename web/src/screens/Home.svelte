<script lang="ts">
  // Home (§3.1): greeting + avatar, then Start radio, Your devices, Recently played, Continue listening, Pinned.
  import { Laptop, Monitor, Radio, Smartphone, Speaker } from '@lucide/svelte';
  import Avatar from '../components/Avatar.svelte';
  import Card from '../components/Card.svelte';
  import QuickTile from '../components/QuickTile.svelte';
  import Shelf from '../components/Shelf.svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { library } from '../lib/library.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Shelf as ShelfT } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import { arrangeShelves, greeting } from '../lib/v3';

  let feed = $state<ShelfT[] | null>(null);
  let error = $state<string | null>(null);

  $effect(() => {
    session.api
      .home()
      .then((f) => (feed = f.shelves))
      .catch((e) => {
        error = e.status === 404 ? null : e.message;
        feed = [
          { id: 'radio', title: 'Start radio', items: [] },
          { id: 'devices', title: 'Your devices', items: [] },
          { id: 'pinned', title: 'Pinned', items: [] },
        ];
      });
    void library.refreshPins();
  });

  const others = $derived(player.devices.filter((d) => d.online && d.id !== player.myDeviceId));
  const shelves = $derived(
    arrangeShelves(
      (feed ?? []).map((s) =>
        s.id === 'pinned' && !s.items.length
          ? { ...s, items: library.pins.map((p) => ({ type: p.type, id: p.id, title: p.title, subtitle: p.subtitle, albumId: p.albumId })) }
          : s,
      ),
      appearance.prefs.homeShelves,
      new Set(others.length ? ['devices'] : []),
    ),
  );
  const kindIcon = (k: string) => (k === 'android' || k === 'watch' ? Smartphone : k === 'desktop' ? Laptop : k === 'web' ? Monitor : Speaker);
</script>

<div class="page home">
  <header class="hello">
    <h1>{greeting(new Date().getHours())}{session.saved.username ? `, ${session.saved.username}` : ''}</h1>
    {#if appearance.form === 'phone'}
      <button aria-label="Profile and settings" onclick={() => ui.open({ kind: 'profile' })}><Avatar name={session.saved.username ?? '?'} size={38} /></button>
    {/if}
  </header>
  {#if error}<p class="muted pad">{error}</p>{/if}
  {#if !feed}<div class="empty">Loading…</div>{/if}

  {#each shelves as s (s.id)}
    {#if s.id === 'radio'}
      <div class="pad">
        <button class="radio-btn" onclick={() => player.startRadio({ type: 'random' }, 'a random radio')}>
          <span class="ico"><Radio size={30} /></span>
          <span><b>Start a random radio</b><br /><span class="muted">Endless music picked from your taste</span></span>
        </button>
      </div>
    {:else if s.id === 'devices'}
      <h2 class="h">Your devices</h2>
      <div class="devices">
        {#each others as d (d.id)}
          {@const Icon = kindIcon(d.kind)}
          {@const playing = player.remoteState?.activeDeviceId === d.id}
          <button class="dev" onclick={() => player.myDeviceId && player.transfer(player.myDeviceId)} disabled={!playing} title={playing ? 'Take over here' : ''}>
            <Icon size={28} />
            <span class="grow">
              <b class="ellipsis">{d.name}</b>
              <span class="muted ellipsis">{playing && player.remoteTrack ? `${player.remoteState?.playing ? 'Playing' : 'Paused'}: ${player.remoteTrack.title} · ${player.remoteTrack.artist}` : 'Idle'}</span>
            </span>
            {#if playing}<span class="take">Play here</span>{/if}
          </button>
        {/each}
      </div>
    {:else if s.id === 'recent'}
      <h2 class="h">{s.title}</h2>
      <div class="quick">{#each s.items.slice(0, appearance.form === 'phone' ? 6 : 8) as it (it.type + it.id)}<QuickTile item={it} />{/each}</div>
    {:else}
      <Shelf title={s.title}>
        {#each s.items as it (it.type + it.id)}<Card item={it} siblings={s.items} />{/each}
      </Shelf>
    {/if}
  {/each}
</div>

<style>
  .home {
    --gutter: clamp(16px, 3vw, 32px);
    padding-bottom: 32px;
  }
  .hello {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px var(--gutter) 8px;
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 18%, transparent), transparent);
  }
  h1 {
    margin: 0;
    font-size: clamp(1.6rem, 3vw, 2.2rem);
  }
  .pad {
    padding: 4px var(--gutter);
  }
  .h {
    margin: 22px var(--gutter) 10px;
    font-size: 1.45rem;
  }
  .radio-btn {
    display: flex;
    align-items: center;
    gap: 18px;
    width: 100%;
    max-width: 560px;
    text-align: left;
    padding: 16px 20px;
    border-radius: 14px;
    background: linear-gradient(120deg, color-mix(in srgb, var(--accent) 35%, #000), #161616);
    border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  }
  .radio-btn:hover {
    filter: brightness(1.15);
  }
  .ico {
    width: 56px;
    height: 56px;
    border-radius: 50%;
    background: var(--accent);
    color: #000;
    display: grid;
    place-items: center;
    flex: none;
  }
  .quick {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
    padding: 0 var(--gutter);
  }
  @media (min-width: 900px) {
    .quick {
      grid-template-columns: repeat(4, 1fr);
    }
  }
  .devices {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 8px;
    padding: 0 var(--gutter);
  }
  .dev {
    display: flex;
    gap: 14px;
    align-items: center;
    text-align: left;
    padding: 12px 16px;
    border-radius: 12px;
    background: var(--bg-2);
  }
  .dev:disabled {
    opacity: 1;
  }
  .grow {
    display: grid;
    min-width: 0;
    flex: 1;
  }
  .take {
    color: var(--accent);
    font-weight: 700;
    font-size: 0.85rem;
  }
</style>
