<script lang="ts">
	import { base } from '$app/paths';
</script>

<header>
	<strong><a href="{base}/">fivethirtyeightindex</a></strong>
</header>

<style>
	header {
		margin: 0 0 var(--space-sm);
		padding: var(--space-md) 0 0;
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-md);
	}

	strong {
		font-size: var(--font-size-brand);
		font-weight: var(--font-weight-display);
		flex: 1 1 auto;
	}

	strong a,
	strong a:visited {
		color: var(--color-fg);
		font-weight: var(--font-weight-display);
	}

	strong a:hover {
		color: var(--color-link);
		text-decoration: none;
	}
</style>
