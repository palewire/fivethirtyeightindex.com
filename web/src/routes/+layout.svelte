<script lang="ts">
	import { env } from '$env/dynamic/public';
	import type { Snippet } from 'svelte';
	import '../app.css';
	import { SiteFooter, SiteHeader, Tagline } from '$lib/components';
	import type { LayoutData } from './$types';

	let { children, data }: { children: Snippet; data: LayoutData } = $props();

	const SITE_URL = 'https://fivethirtyeightindex.com';
	const SOCIAL_IMAGE = `${SITE_URL}/abacus.png`;
	const SOCIAL_TITLE = 'fivethirtyeightindex.com';
	const SOCIAL_DESCRIPTION =
		'An index of every fivethirtyeight.com article preserved by the Internet Archive.';
	const CLOUDFLARE_TOKEN = env.PUBLIC_CLOUDFLARE_WEB_ANALYTICS_TOKEN || '';
	const CLOUDFLARE_BEACON = JSON.stringify({
		token: CLOUDFLARE_TOKEN
	});
</script>

<svelte:head>
	<meta property="og:type" content="website" />
	<meta property="og:title" content={SOCIAL_TITLE} />
	<meta property="og:description" content={SOCIAL_DESCRIPTION} />
	<meta property="og:image" content={SOCIAL_IMAGE} />
	<meta property="og:url" content={SITE_URL} />
	<meta name="twitter:card" content="summary" />
	<meta name="twitter:title" content={SOCIAL_TITLE} />
	<meta name="twitter:description" content={SOCIAL_DESCRIPTION} />
	<meta name="twitter:image" content={SOCIAL_IMAGE} />
	{#if CLOUDFLARE_TOKEN}
		<script
			defer
			src="https://static.cloudflareinsights.com/beacon.min.js"
			data-cf-beacon={CLOUDFLARE_BEACON}
		></script>
	{/if}
</svelte:head>

<div class="page">
	<SiteHeader />
	<Tagline total={data.total} />
	<main>
		{@render children()}
	</main>
	<SiteFooter />
</div>
