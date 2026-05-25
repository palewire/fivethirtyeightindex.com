<script lang="ts">
	import { base } from '$app/paths';
	import { categoryLabel, graphicCategoryGroup, slugify } from '$lib/data';
	import type { Graphic } from '$lib/types';

	interface Props {
		graphics: Graphic[];
		label: string;
		showCategory?: boolean;
		showItemLabel?: boolean;
		imageFit?: 'contain' | 'cover';
		fallbackLabel?: string;
		showFallbackThumbLabel?: boolean;
	}

	let {
		graphics,
		label,
		showCategory = true,
		showItemLabel = true,
		imageFit = 'contain',
		fallbackLabel = 'Graphic',
		showFallbackThumbLabel = true
	}: Props = $props();

	function fmtDate(iso: string): string {
		if (!iso) return '';
		return iso.slice(0, 10);
	}

	function itemLabel(graphic: Graphic): string {
		return showCategory ? categoryLabel(graphicCategoryGroup(graphic.category)) : fallbackLabel;
	}

</script>

<section class="graphics-grid" aria-label={label}>
	{#each graphics as graphic (graphic.id)}
		{@const graphicAuthors = graphic.article_authors ?? []}
		<article class="graphic">
			<a class="thumb" href={graphic.url} rel="noopener external" target="_blank">
				{#if graphic.thumbnail_url}
					<img
						class:cover={imageFit === 'cover'}
						src={graphic.thumbnail_url}
						alt={graphic.description || graphic.title}
						loading="lazy"
					/>
				{:else if showFallbackThumbLabel}
					<span>{itemLabel(graphic)}</span>
				{/if}
			</a>
			<div class="graphic-body">
				<div class="meta">
					{#if showItemLabel}
						<span>{itemLabel(graphic)}</span>
					{/if}
					{#if graphic.date}
						<span><time datetime={graphic.date}>{fmtDate(graphic.date)}</time></span>
					{/if}
				</div>
				<h2>
					<a href={graphic.url} rel="noopener external" target="_blank">{graphic.title}</a>
				</h2>
				{#if graphic.article_title}
					<p class="source">
						From
						<a href={graphic.article_url || graphic.url} rel="noopener external" target="_blank">
							{graphic.article_title}
						</a>
						{#if graphicAuthors.length > 0}
							by
							{#each graphicAuthors as name, i (name)}
								<a href="{base}/byline/{slugify(name)}/">{name}</a>{i < graphicAuthors.length - 1
									? ', '
									: ''}
							{/each}
						{:else if graphic.byline}
							by {graphic.byline}
						{/if}
					</p>
				{/if}
			</div>
		</article>
	{/each}
</section>

<style>
	.graphics-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
		gap: 1rem;
		margin: 0 0 var(--space-lg);
	}

	.graphic {
		min-width: 0;
		border-bottom: var(--rule-thin);
		padding-bottom: 0.7rem;
	}

	.thumb {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 100%;
		aspect-ratio: 4 / 3;
		border: var(--rule-thin);
		background: #f7f7f7;
		overflow: hidden;
	}

	.thumb:hover {
		border-color: var(--color-fg);
	}

	.thumb img {
		display: block;
		width: 100%;
		height: 100%;
		object-fit: contain;
	}

	.thumb img.cover {
		object-fit: cover;
	}

	.thumb span {
		color: var(--color-muted);
		font-size: var(--font-size-table);
	}

	.graphic-body {
		padding-top: 0.45rem;
	}

	.meta {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem 0.65rem;
		color: var(--color-muted);
		font-size: var(--font-size-meta);
		text-transform: uppercase;
	}

	h2 {
		margin: 0.2rem 0 0;
		font-size: var(--font-size-table);
		font-weight: var(--font-weight-regular);
		line-height: var(--line-height-tight);
	}

	h2 a {
		color: var(--color-fg);
	}

	.source {
		margin: 0.25rem 0 0;
		color: var(--color-muted);
		font-size: var(--font-size-meta);
		line-height: var(--line-height-tight);
	}

	.source a {
		color: var(--color-link);
	}

	@media (min-width: 900px) {
		.graphics-grid {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
	}

	@media (max-width: 640px) {
		.graphics-grid {
			grid-template-columns: repeat(auto-fill, minmax(145px, 1fr));
			gap: 0.8rem;
		}
	}
</style>
