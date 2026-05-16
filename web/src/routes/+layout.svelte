<script lang="ts">
	import '../app.css';
	import { SiteFooter, SiteHeader } from '$lib/components';
	let { children } = $props();
</script>

<div class="page">
	<SiteHeader />
	{@render children()}
	<SiteFooter />
</div>
