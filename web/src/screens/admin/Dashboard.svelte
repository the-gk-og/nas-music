<script lang="ts">
  import { Pause, Play, Speaker } from '@lucide/svelte';
  import { adminApi, etaText, uptimeText, type NowPlayingRow, type ServerStatus } from '../../lib/api/admin';
  import { fmtBytes, fmtTime } from '../../lib/format';
  import { session } from '../../lib/session.svelte';
  import JobsTable from './JobsTable.svelte';

  const A = $derived(adminApi(session.api));
  let st = $state<ServerStatus | null>(null);
  let np = $state<NowPlayingRow[]>([]);
  let error = $state<string | null>(null);

  async function poll() {
    try {
      [st, np] = await Promise.all([A.status(), A.nowPlaying().catch(() => [])]);
      error = null;
    } catch (e) {
      error = (e as { status?: number }).status === 404 ? 'This server has no admin status endpoint yet.' : (e as Error).message;
    }
  }
  $effect(() => {
    void poll();
    const h = setInterval(() => document.visibilityState === 'visible' && void poll(), 5000);
    return () => clearInterval(h);
  });

  const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
  const level = (p: number) => (p >= 90 ? 'bad' : p >= 75 ? 'warn' : '');
  const nightly = $derived(st?.jobs.find((j) => j.name === 'nightly'));
</script>

<h2>Dashboard</h2>
{#if error}<p class="bad">{error}</p>{/if}
{#if !st && !error}<p class="muted">Loading…</p>{/if}
{#if st}
  <div class="grid">
    <div class="tile">
      <div class="k">CPU</div>
      <div class="v">{st.cpuPct}%</div>
      <div class="s">{st.load ? `load ${st.load.map((x) => x.toFixed(2)).join(' · ')}` : ''}</div>
      <div class="meter {level(st.cpuPct)}"><span style:width="{st.cpuPct}%"></span></div>
    </div>
    <div class="tile">
      <div class="k">Memory</div>
      <div class="v">{pct(st.memUsedBytes, st.memTotalBytes)}%</div>
      <div class="s">{fmtBytes(st.memUsedBytes)} of {fmtBytes(st.memTotalBytes)}</div>
      <div class="meter {level(pct(st.memUsedBytes, st.memTotalBytes))}"><span style:width="{pct(st.memUsedBytes, st.memTotalBytes)}%"></span></div>
    </div>
    {#each st.disks as d (d.path)}
      {@const p = pct(d.usedBytes, d.totalBytes)}
      <div class="tile">
        <div class="k">{d.name}</div>
        <div class="v">{p}%</div>
        <div class="s"><span class="mono">{d.path}</span> · {fmtBytes(d.usedBytes)} of {fmtBytes(d.totalBytes)}</div>
        <div class="meter {level(p)}"><span style:width="{p}%"></span></div>
      </div>
    {/each}
    <div class="tile">
      <div class="k">Server</div>
      <div class="v">{st.version}</div>
      <div class="s">up {uptimeText(st.uptimeS)} · data {fmtBytes(st.dataBytes)}</div>
      <div class="s">{st.library.tracks} tracks · {st.library.albums} albums · {st.library.artists} artists{st.library.missing ? ` · ${st.library.missing} missing` : ''}</div>
    </div>
    <div class="tile">
      <div class="k">Analysis</div>
      <div class="v">{pct(st.analysis.done, st.analysis.total)}%</div>
      <div class="s">{st.analysis.done} / {st.analysis.total}{st.analysis.failed ? ` · ${st.analysis.failed} failed` : ''}</div>
      <div class="s">{st.analysis.done >= st.analysis.total ? 'Done' : `ETA ${etaText(st.analysis.etaS)}${st.analysis.ratePerHour ? ` · ${st.analysis.ratePerHour}/h` : ''}`}</div>
      <div class="meter"><span style:width="{pct(st.analysis.done, st.analysis.total)}%"></span></div>
    </div>
    <div class="tile">
      <div class="k">Last nightly run</div>
      {#if nightly?.finishedAt}
        <div class="v {nightly.ok ? 'ok' : 'bad'}">{nightly.ok ? 'OK' : 'Failed'}</div>
        <div class="s">{new Date(nightly.finishedAt).toLocaleString()}{nightly.durationS ? ` · ${Math.round(nightly.durationS)} s` : ''}</div>
        {#if nightly.error}<div class="s bad">{nightly.error}</div>{/if}
      {:else}<div class="v">—</div><div class="s">Not run yet</div>{/if}
    </div>
  </div>

  <h3>Now playing</h3>
  {#if np.length}
    <div class="tablewrap">
      <table>
        <thead><tr><th>User</th><th>Device</th><th>Track</th><th>Position</th></tr></thead>
        <tbody>
          {#each np as r (r.deviceId)}
            <tr>
              <td>{r.username}</td>
              <td><span class="row"><Speaker size={14} />{r.deviceName}</span></td>
              <td>{#if r.track}<span class="row">{#if r.playing}<Play size={14} class="ok" />{:else}<Pause size={14} />{/if}{r.track.title} · <span class="muted">{r.track.artist}</span></span>{:else}<span class="muted">idle</span>{/if}</td>
              <td class="mono">{r.track ? `${fmtTime(r.positionMs / 1000)} / ${fmtTime(r.track.durationS)}` : ''}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}<p class="muted">Nobody is connected.</p>{/if}

  <h3>Jobs</h3>
  <JobsTable jobs={st.jobs} onchange={poll} />
{/if}
