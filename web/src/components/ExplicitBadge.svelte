<script lang="ts">
  // "E" next to explicit tracks / albums (v6). `probable` (lyrics word list) gets a faded, dashed variant.
  let { status = 'explicit' }: { status?: 'explicit' | 'probable' | boolean | string | null | undefined } = $props();
  const kind = $derived(status === true || status === 'explicit' ? 'explicit' : status === 'probable' ? 'probable' : null);
</script>

{#if kind}
  <span class="e" class:probable={kind === 'probable'} title={kind === 'explicit' ? 'Explicit' : 'Probably explicit'}
    aria-label={kind === 'explicit' ? 'Explicit' : 'Probably explicit'}>E</span>
{/if}

<style>
  .e {
    display: inline-grid;
    place-items: center;
    flex: none;
    width: 1.15em;
    height: 1.15em;
    margin-right: 0.35em;
    border-radius: 3px;
    background: var(--muted, #888);
    color: var(--bg, #000);
    font-size: 0.72em;
    font-weight: 700;
    line-height: 1;
    vertical-align: 0.12em;
  }
  .e.probable {
    background: transparent;
    color: var(--muted, #888);
    border: 1px dashed currentColor;
  }
</style>
