<script lang="ts">
	import '../app.css';
	import { SiteFooter, SiteHeader } from '$lib/components';
	let { children } = $props();

	const SITE_URL = 'https://fivethirtyeightindex.com';
	const SOCIAL_IMAGE = `${SITE_URL}/abacus.png`;
	const SOCIAL_TITLE = 'fivethirtyeightindex.com';
	const SOCIAL_DESCRIPTION =
		'An index of every fivethirtyeight.com article preserved by the Internet Archive.';
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
</svelte:head>

<div class="page">
	<SiteHeader />
	{@render children()}
	<SiteFooter />
</div>
