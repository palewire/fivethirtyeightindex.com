<script lang="ts">
	import { base } from '$app/paths';
	import { thumbnailUrl } from '$lib/thumbnail';
	import type { Graphic } from '$lib/types';

	interface Props {
		graphics: Graphic[];
		total: number;
		title?: string;
		href?: string;
		label?: string;
	}

	let { graphics, total, title = 'Graphics', href = '/graphics/', label = 'graphics' }: Props = $props();
	let rail = $derived([...graphics, ...graphics]);
</script>

{#if total > 0}
	<h2 class="section-heading">{title}</h2>
	<div class="graphics-teaser">
		<div class="rail" aria-label="Sample graphics">
			{#each rail as graphic, i (`${graphic.id}-${i}`)}
				<a class="tile" href={graphic.url} rel="noopener external" target="_blank">
					{#if graphic.thumbnail_url}
						<img
							src={thumbnailUrl(graphic.thumbnail_url)}
							alt={graphic.description || graphic.title}
							loading="lazy"
						/>
					{:else}
						<span>{graphic.category}</span>
					{/if}
				</a>
			{/each}
		</div>
	</div>
	<div class="browse-block graphics-link">
		<ul class="browse-list">
			<li class="see-all">
				<a href="{base}{href}">See all {total.toLocaleString()} {label} -></a>
			</li>
		</ul>
	</div>
{/if}

<style>
	.graphics-teaser {
		overflow: hidden;
		margin-bottom: var(--space-sm);
	}

	.rail {
		display: flex;
		flex-wrap: nowrap;
		gap: 0.55rem;
		width: max-content;
		animation: graphic-marquee 42s linear infinite;
	}

	.graphics-teaser:hover .rail {
		animation-play-state: paused;
	}

	.tile {
		display: flex;
		align-items: center;
		justify-content: center;
		flex: 0 0 92px;
		width: 92px;
		aspect-ratio: 1;
		border: var(--rule-thin);
		background: #f7f7f7;
		overflow: hidden;
	}

	.tile:hover {
		border-color: var(--color-fg);
	}

	.tile img {
		display: block;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.tile span {
		color: var(--color-muted);
		font-size: var(--font-size-meta);
		text-align: center;
	}

	.graphics-link {
		margin-bottom: var(--space-lg);
	}

	.see-all a {
		color: var(--color-fg);
		font-weight: var(--font-weight-regular);
	}

	.see-all a:hover {
		color: var(--color-link);
		text-decoration: underline;
	}

	@keyframes graphic-marquee {
		from {
			transform: translateX(0);
		}
		to {
			transform: translateX(calc(-50% - 0.275rem));
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.rail {
			animation: none;
		}
	}

	@media (max-width: 640px) {
		.tile {
			flex-basis: 78px;
			width: 78px;
		}
	}
</style>
