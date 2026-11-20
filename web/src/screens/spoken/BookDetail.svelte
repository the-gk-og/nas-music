<script lang="ts">
  // One audiobook: play / resume, chapters, bookmarks.
  import { ArrowLeft, Bookmark, Play, Trash2 } from '@lucide/svelte';
  import SpokenArt from '../../components/SpokenArt.svelte';
  import { session } from '../../lib/session.svelte';
  import { chapterAt, clock, remaining, spokenApi, type AudiobookDetail } from '../../lib/spoken';
  import { spoken } from '../../lib/spokenPlayer.svelte';
  import { ui } from '../../lib/ui.svelte';

  let { id }: { id: string } = $props();
  const api = $derived(spokenApi(session.api));
  let book = $state<AudiobookDetail | null>(null);
  let error = $state('');
  const load = () => api.book(id).then((b) => (book = b)).catch((e) => (error = e.message));
  $effect(() => void load());

  const current = $derived(spoken.current?.kind === 'book' && spoken.current.id === id);
  const pos = $derived(current ? spoken.position : (book?.progress?.positionS ?? 0));
  const chIdx = $derived(book ? chapterAt(book.chapters.map((c) => c.startS), pos) : -1);

  async function playAt(t?: number) {
    if (!book) return;
    if (current && t !== undefined) await spoken.seek(t);
    else if (current) spoken.toggle();
    else await spoken.playBook(id, book, t);
  }
  async function addBookmark() {
    const note = await ui.prompt('Bookmark', `Note for ${clock(pos)} (optional)`, '', 'Add');
    if (note === null) return;
    await api.addBookmark(id, pos, note).catch((e) => ui.toast(e.message));
    await load();
  }
  async function delBookmark(bid: string) {
    await api.deleteBookmark(id, bid).catch((e) => ui.toast(e.message));
    await load();
  }
</script>

<div class="page book">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
  </div>
  {#if error}<div class="empty">{error}</div>{/if}
  {#if book}
    <header>
      <SpokenArt kind="book" src={api.artSrc('book', book.id, 512)} size={180} />
      <div class="grow">
        <h1>{book.title}</h1>
        <div class="muted">{book.author ?? ''}{book.narrator ? ` · read by ${book.narrator}` : ''}{book.year ? ` · ${book.year}` : ''}</div>
        <div class="muted small">{clock(book.durationS)} · {remaining(book.progress, book.durationS) || 'Not started'}</div>
        <div class="tools">
          <button class="btn" onclick={() => playAt()}><Play size={18} /> {current && spoken.playing ? 'Pause' : book.progress && book.progress.positionS > 5 && !book.progress.played ? 'Resume' : 'Play'}</button>
          <button class="btn ghost" onclick={addBookmark}><Bookmark size={16} /> Bookmark</button>
        </div>
        {#if book.description}<p class="small muted desc">{book.description}</p>{/if}
      </div>
    </header>

    {#if book.bookmarks.length}
      <h2>Bookmarks</h2>
      {#each book.bookmarks as b (b.id)}
        <div class="row">
          <button class="grow txt" onclick={() => playAt(b.positionS)}><b>{clock(b.positionS)}</b> <span class="muted">{b.note ?? ''}</span></button>
          <button class="icon-btn" aria-label="Delete bookmark" onclick={() => delBookmark(b.id)}><Trash2 size={16} /></button>
        </div>
      {/each}
    {/if}

    <h2>Chapters</h2>
    {#each book.chapters as c, i (c.index)}
      <button class="row txt" class:now={i === chIdx} onclick={() => playAt(c.startS)}>
        <span class="n muted">{i + 1}</span><span class="grow ellipsis">{c.title}</span><span class="muted small">{clock(c.startS)}</span>
      </button>
    {/each}
  {:else if !error}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .book {
    padding-bottom: 120px;
  }
  header {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  h1 {
    margin: 0 0 4px;
  }
  h2 {
    font-size: 1.1rem;
    margin: 20px 0 8px;
  }
  .desc {
    max-width: 70ch;
    white-space: pre-line;
  }
  .tools {
    display: flex;
    gap: 10px;
    margin: 12px 0;
  }
  .btn {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    padding: 8px 16px;
    border-radius: 999px;
    background: var(--accent);
    color: #000;
    font-weight: 600;
  }
  .btn.ghost {
    background: none;
    color: inherit;
    border: 1px solid #555;
  }
  .row {
    display: flex;
    gap: 12px;
    align-items: center;
    width: 100%;
    padding: 8px 0;
    border-bottom: 1px solid var(--line, #2a2a2a);
    text-align: left;
  }
  .row.now {
    color: var(--accent);
  }
  .n {
    width: 2em;
    text-align: right;
  }
</style>
