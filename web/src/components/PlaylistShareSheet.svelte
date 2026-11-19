<script lang="ts">
  // Collaborative playlist (v5): members, roles, invite, leave, activity.
  import { LogOut, UserPlus, Users, X } from '@lucide/svelte';
  import { library } from '../lib/library.svelte';
  import { session } from '../lib/session.svelte';
  import type { Playlist, PlaylistActivity, PlaylistMember } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Avatar from './Avatar.svelte';
  import Sheet from './Sheet.svelte';

  let { playlistId }: { playlistId: string } = $props();

  let pl = $state<Playlist | null>(null);
  let members = $state<PlaylistMember[] | null>(null);
  let users = $state<{ id: string; username: string }[]>([]);
  let activity = $state<PlaylistActivity[] | null>(null);
  let pick = $state('');
  let role = $state<'editor' | 'viewer'>('editor');
  let error = $state<string | null>(null);
  const isOwner = $derived(pl?.role === 'owner');

  async function load() {
    try {
      [pl, members, activity] = await Promise.all([
        session.api.playlist(playlistId),
        session.api.playlistMembers(playlistId),
        session.api.playlistActivity(playlistId, 50),
      ]);
      if (pl.role === 'owner' && !users.length) users = await session.api.userDirectory();
    } catch (e) {
      error = (e as Error).message;
    }
  }
  $effect(() => {
    void playlistId;
    void load();
  });
  // live: someone else changed it
  $effect(() => {
    const c = library.changed;
    if (c && c.id === playlistId) {
      if (c.deleted) ui.close();
      else void load();
    }
  });

  const candidates = $derived(users.filter((u) => !members?.some((m) => m.userId === u.id)));

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  const invite = () => pick && act(() => session.api.addPlaylistMember(playlistId, { userId: pick, role }).then(() => (pick = '')));
  const setRole = (m: PlaylistMember, r: 'editor' | 'viewer') => act(() => session.api.setPlaylistMemberRole(playlistId, m.userId, r));
  async function remove(m: PlaylistMember) {
    if (!(await ui.confirm('Remove member', `Remove ${m.username} from “${pl?.name}”? Songs they added stay.`, 'Remove'))) return;
    await act(() => session.api.removePlaylistMember(playlistId, m.userId));
  }
  async function leave() {
    if (!(await ui.confirm('Leave playlist', `Leave “${pl?.name}”? You won't see it any more.`, 'Leave'))) return;
    try {
      const me = members?.find((m) => m.username === session.saved.username);
      if (me) await session.api.removePlaylistMember(playlistId, me.userId);
      ui.close();
      void library.refreshPlaylists();
      ui.go('library', 'playlists');
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  function describe(a: PlaylistActivity): string {
    const n = Number(a.detail?.count ?? 1);
    const what = a.trackTitle ? `“${a.trackTitle}”${n > 1 ? ` and ${n - 1} more` : ''}` : `${n} song${n === 1 ? '' : 's'}`;
    switch (a.action) {
      case 'create': return 'created the playlist';
      case 'rename': return `renamed it to “${a.detail?.to}”`;
      case 'add': return `added ${what}`;
      case 'remove': return `removed ${what}`;
      case 'move': return `moved ${what}`;
      case 'member_add': return `invited ${a.detail?.user} as ${a.detail?.role}`;
      case 'member_role': return `made ${a.detail?.user} ${a.detail?.role === 'editor' ? 'an editor' : 'a viewer'}`;
      case 'member_remove': return `removed ${a.detail?.user}`;
      case 'leave': return 'left';
      default: return a.action;
    }
  }
</script>

<Sheet label="Share playlist">
  <div class="pad">
    <h2 class="title"><Users size={26} /> {pl?.name ?? 'Playlist'}</h2>
    {#if error}<p class="error">{error}</p>
    {:else if !members}<p class="muted">Loading…</p>
    {:else}
      <p class="muted small">Editors can add, remove and reorder songs. Viewers can only listen. Changes show up live for everyone.</p>
      {#each members as m (m.userId)}
        <div class="mem">
          <Avatar name={m.username} size={32} />
          <div class="grow"><b>{m.username}</b></div>
          {#if m.role === 'owner'}<span class="muted small">Owner</span>
          {:else if isOwner}
            <select value={m.role} onchange={(e) => setRole(m, (e.target as HTMLSelectElement).value as 'editor' | 'viewer')} aria-label="Role of {m.username}">
              <option value="editor">Editor</option>
              <option value="viewer">Viewer</option>
            </select>
            <button class="icon-btn" aria-label="Remove {m.username}" onclick={() => remove(m)}><X size={18} /></button>
          {:else}<span class="muted small">{m.role === 'editor' ? 'Editor' : 'Viewer'}</span>{/if}
        </div>
      {/each}
      {#if isOwner}
        <div class="invite">
          <select bind:value={pick} aria-label="User to invite">
            <option value="">Invite someone…</option>
            {#each candidates as u (u.id)}<option value={u.id}>{u.username}</option>{/each}
          </select>
          <select bind:value={role} aria-label="Role"><option value="editor">Editor</option><option value="viewer">Viewer</option></select>
          <button class="btn" disabled={!pick} onclick={invite}><UserPlus size={16} /> Invite</button>
        </div>
        {#if !candidates.length}<p class="muted small">Everyone on this server is already in.</p>{/if}
      {:else}
        <button class="btn text" onclick={leave}><LogOut size={16} /> Leave playlist</button>
      {/if}
      <h3>Activity</h3>
      {#each activity ?? [] as a (a.id)}
        <div class="act small"><b>{a.user ?? 'Someone'}</b> {describe(a)} <span class="muted">· {new Date(a.at).toLocaleString()}</span></div>
      {:else}<p class="muted small">Nothing yet.</p>{/each}
    {/if}
  </div>
  <div class="foot"><span></span><button class="btn text" onclick={() => ui.close()}>Done</button></div>
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
  .mem {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 0;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .invite {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 12px 0;
  }
  .act {
    padding: 4px 0;
  }
  .foot {
    display: flex;
    justify-content: space-between;
    padding: 10px 16px;
  }
</style>
