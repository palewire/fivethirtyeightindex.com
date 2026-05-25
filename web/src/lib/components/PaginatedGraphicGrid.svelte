<script lang="ts">
	import GraphicGrid from './GraphicGrid.svelte';
	import type { Graphic } from '$lib/types';

	interface Props {
		graphics: Graphic[];
		label: string;
		showCategory?: boolean;
		showItemLabel?: boolean;
		imageFit?: 'contain' | 'cover';
		fallbackLabel?: string;
		showFallbackThumbLabel?: boolean;
		pageSize?: number;
	}

	const DEFAULT_PAGE_SIZE = 96;

	let {
		graphics,
		label,
		showCategory = true,
		showItemLabel = true,
		imageFit = 'contain',
		fallbackLabel = 'Graphic',
		showFallbackThumbLabel = true,
		pageSize = DEFAULT_PAGE_SIZE
	}: Props = $props();

	let visibleCount = $state(DEFAULT_PAGE_SIZE);
	let visibleGraphics = $derived(graphics.slice(0, visibleCount));
	let hasMore = $derived(visibleCount < graphics.length);

	$effect(() => {
		graphics;
		visibleCount = pageSize;
	});

	function showMore(): void {
		visibleCount = Math.min(visibleCount + pageSize, graphics.length);
	}
</script>

<GraphicGrid
	graphics={visibleGraphics}
	{label}
	{showCategory}
	{showItemLabel}
	{imageFit}
	{fallbackLabel}
	{showFallbackThumbLabel}
/>

{#if hasMore}
	<div class="pagination">
		<p>
			Showing {visibleGraphics.length.toLocaleString()} of {graphics.length.toLocaleString()}
		</p>
		<button type="button" onclick={showMore}>Load more</button>
	</div>
{/if}

<style>
	.pagination {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.45rem;
		margin: 0.5rem 0 var(--space-lg);
		color: var(--color-muted);
		font-size: var(--font-size-table);
	}

	.pagination p {
		margin: 0;
	}

	.pagination button {
		border: var(--rule-thin);
		border-radius: 4px;
		background: var(--color-bg);
		color: var(--color-link);
		font: inherit;
		padding: 0.3rem 0.55rem;
		cursor: pointer;
	}

	.pagination button:hover {
		color: var(--color-fg);
		border-color: var(--color-fg);
	}
</style>
