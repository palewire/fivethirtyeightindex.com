<script lang="ts">
	interface Props {
		total: number;
	}
	let { total }: Props = $props();
</script>

<p>
	Search {total.toLocaleString()} pages from fivethirtyeight.com preserved by the Internet Archive
</p>

<style>
	p {
		color: var(--color-muted);
		font-weight: var(--font-weight-light);
		margin: 0 0 var(--space-lg);
	}
</style>
