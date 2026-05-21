<script lang="ts">
	import { base } from '$app/paths';

	interface Byline {
		slug: string;
		name: string;
		count: number;
	}

	interface Props {
		bylines: Byline[];
		total: number;
	}
	let { bylines, total }: Props = $props();
</script>

<h2 class="section-heading">Browse by byline</h2>
<div class="browse-block">
	<ul class="browse-list">
		{#each bylines as b (b.slug)}
			<li>
				<a href="{base}/byline/{b.slug}/">{b.name}</a><span class="count"
					>({b.count})</span
				>
			</li>
		{/each}
		<li class="see-all">
			<a href="{base}/byline/">See all {total.toLocaleString()} bylines -></a>
		</li>
	</ul>
</div>

<style>
	.see-all a {
		color: var(--color-fg);
		font-weight: var(--font-weight-regular);
	}

	.see-all a:hover {
		color: var(--color-link);
		text-decoration: underline;
	}
</style>
