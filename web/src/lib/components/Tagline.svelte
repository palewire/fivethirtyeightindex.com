<script lang="ts">
	interface Props {
		total: number;
	}
	let { total }: Props = $props();
</script>

<p>
	Explore {total.toLocaleString()} articles, datasets, podcasts, graphics and illustrations from
	fivethirtyeight.com
</p>

<style>
	p {
		color: var(--color-muted);
		font-weight: var(--font-weight-light);
		margin: 0 0 var(--space-lg);
	}
</style>
