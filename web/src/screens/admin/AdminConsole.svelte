<script lang="ts">
  // Admin console: desktop-first (section list on the left), chips on phones.
  import { Activity, ArrowLeft, Baby, Bug, Copy, DatabaseBackup, HeartPulse, Library, ScrollText, Settings, Tags, Users, WandSparkles } from '@lucide/svelte';
  import { appearance } from '../../lib/appearance.svelte';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';
  import AutotagAdmin from './AutotagAdmin.svelte';
  import Backups from './Backups.svelte';
  import Dashboard from './Dashboard.svelte';
  import FamilyAdmin from './FamilyAdmin.svelte';
  import DuplicatesAdmin from './DuplicatesAdmin.svelte';
  import Health from './Health.svelte';
  import LibraryAdmin from './LibraryAdmin.svelte';
  import Logs from './Logs.svelte';
  import Reports from './Reports.svelte';
  import ServerSettings from './ServerSettings.svelte';
  import TagsAdmin from './TagsAdmin.svelte';
  import UsersSection from './Users.svelte';

  const SECTIONS = [
    { id: 'dashboard', label: 'Dashboard', icon: Activity },
    { id: 'users', label: 'Users & invites', icon: Users },
    { id: 'library', label: 'Library', icon: Library },
    { id: 'tags', label: 'Tags', icon: Tags },
    { id: 'family', label: 'Family & explicit', icon: Baby },
    { id: 'autotag', label: 'Tag fixing', icon: WandSparkles },
    { id: 'duplicates', label: 'Duplicates', icon: Copy },
    { id: 'health', label: 'Health & pruning', icon: HeartPulse },
    { id: 'logs', label: 'Logs & jobs', icon: ScrollText },
    { id: 'reports', label: 'Bug reports', icon: Bug },
    { id: 'backups', label: 'Backups', icon: DatabaseBackup },
    { id: 'settings', label: 'Server settings', icon: Settings },
  ] as const;

  const section = $derived(SECTIONS.some((s) => s.id === ui.route.param) ? ui.route.param : 'dashboard');
  const wide = $derived(appearance.width >= 900);
</script>

{#if !session.isAdmin}
  <div class="page"><div class="empty">Admins only.</div></div>
{:else}
  <div class="console" class:wide>
    <nav class="sections" aria-label="Admin sections">
      <div class="head">
        <button class="icon-btn" aria-label="Back" onclick={() => ui.go('home')}><ArrowLeft size={22} /></button>
        <h1>Admin</h1>
      </div>
      <div class="list">
        {#each SECTIONS as s (s.id)}
          {@const Icon = s.icon}
          <button class="sec" class:on={section === s.id} aria-current={section === s.id ? 'page' : undefined} onclick={() => ui.go('admin', s.id)}>
            <Icon size={18} /><span>{s.label}</span>
          </button>
        {/each}
      </div>
    </nav>
    <section class="body">
      {#if section === 'dashboard'}<Dashboard />
      {:else if section === 'users'}<UsersSection />
      {:else if section === 'library'}<LibraryAdmin />
      {:else if section === 'tags'}<TagsAdmin />
      {:else if section === 'family'}<FamilyAdmin />
      {:else if section === 'autotag'}<AutotagAdmin />
      {:else if section === 'duplicates'}<DuplicatesAdmin />
      {:else if section === 'health'}<Health />
      {:else if section === 'logs'}<Logs />
      {:else if section === 'reports'}<Reports />
      {:else if section === 'backups'}<Backups />
      {:else}<ServerSettings />{/if}
    </section>
  </div>
{/if}

<style>
  .console {
    height: 100%;
    display: flex;
    flex-direction: column;
  }
  .console.wide {
    flex-direction: row;
  }
  .sections {
    flex: none;
    border-bottom: 1px solid var(--line);
  }
  .wide .sections {
    width: 230px;
    border-bottom: 0;
    border-right: 1px solid var(--line);
    padding: 8px;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 8px;
  }
  h1 {
    margin: 0;
    font-size: 1.35rem;
  }
  .list {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding: 4px 12px 10px;
    scrollbar-width: none;
  }
  .wide .list {
    flex-direction: column;
    padding: 4px 0;
    gap: 2px;
  }
  .sec {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    border-radius: 999px;
    background: var(--bg-2);
    color: var(--fg-2);
    white-space: nowrap;
    font-size: 0.92rem;
    flex: none;
  }
  .wide .sec {
    border-radius: 8px;
    background: none;
    padding: 10px 12px;
  }
  .sec:hover {
    color: #fff;
  }
  .sec.on {
    background: #fff;
    color: #000;
  }
  .wide .sec.on {
    background: rgba(255, 255, 255, 0.08);
    color: var(--accent);
  }
  .body {
    flex: 1;
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
    padding: 16px clamp(14px, 3vw, 32px) 40px;
  }
  .body :global(h2) {
    font-size: 1.35rem;
    margin: 4px 0 14px;
  }
  .body :global(h3) {
    font-size: 1.05rem;
    margin: 22px 0 10px;
  }
  .body :global(.grid) {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 12px;
  }
  .body :global(.tile) {
    background: var(--bg-1);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 14px 16px;
    min-width: 0;
  }
  .body :global(.tile .k) {
    color: var(--fg-3);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .body :global(.tile .v) {
    font-size: 1.6rem;
    font-weight: 700;
    margin: 4px 0;
    font-variant-numeric: tabular-nums;
  }
  .body :global(.tile .s) {
    color: var(--fg-3);
    font-size: 0.85rem;
  }
  .body :global(.meter) {
    height: 6px;
    background: #2a2a2a;
    border-radius: 3px;
    overflow: hidden;
    margin-top: 8px;
  }
  .body :global(.meter > span) {
    display: block;
    height: 100%;
    background: var(--accent);
    border-radius: 3px;
    transition: width 0.4s;
  }
  .body :global(.meter.warn > span) {
    background: #fbbf24;
  }
  .body :global(.meter.bad > span) {
    background: var(--danger);
  }
  .body :global(table) {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
  }
  .body :global(th) {
    text-align: left;
    color: var(--fg-3);
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 8px 10px;
    border-bottom: 1px solid var(--line);
  }
  .body :global(td) {
    padding: 10px;
    border-bottom: 1px solid var(--line);
    vertical-align: top;
  }
  .body :global(.tablewrap) {
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: 12px;
  }
  .body :global(.ok) {
    color: var(--accent);
  }
  .body :global(.bad) {
    color: var(--danger);
  }
  .body :global(.warnc) {
    color: #fbbf24;
  }
  .body :global(.row) {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .body :global(.btn.sm) {
    padding: 6px 12px;
    font-size: 0.85rem;
  }
  .body :global(.mono) {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.85rem;
  }
  .body :global(.small) {
    font-size: 0.85rem;
  }
</style>
