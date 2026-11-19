<script lang="ts">
  let { name, size = 34 }: { name: string; size?: number } = $props();
  const hue = $derived([...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7));
</script>

<span class="av" style:width="{size}px" style:height="{size}px" style:background="hsl({hue} 45% 35%)" style:font-size="{size * 0.45}px">{name.slice(0, 1).toUpperCase()}</span>

<style>
  .av {
    display: inline-grid;
    place-items: center;
    border-radius: 50%;
    font-weight: 700;
    color: #fff;
    flex: none;
  }
</style>
