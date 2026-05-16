<script lang="ts">
	import '../app.css';
	import { base } from '$app/paths';
	let { children } = $props();
</script>

<div class="page">
	<header class="site-header">
		<strong><a href="{base}/">fakethirtyeight.com</a></strong>
		<nav>
			<a href="{base}/">index</a>
			<a href="{base}/byline/">bylines</a>
		</nav>
	</header>

	{@render children()}

	<footer>
		An archive of fivethirtyeight.com built from
		<a href="https://web.archive.org/" rel="noopener external" target="_blank"
			>archive.org's Wayback Machine</a
		>. Headlines link to Wayback snapshots — articles are not republished here.
		Source on
		<a
			href="https://github.com/palewire/fakethirtyeight.com"
			rel="noopener external"
			target="_blank">GitHub</a
		>.
	</footer>
</div>
