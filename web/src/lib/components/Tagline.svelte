<script lang="ts">
	import { base } from '$app/paths';

	interface Props {
		total: number;
	}
	let { total }: Props = $props();
</script>

<p class="tagline">
	An index of every fivethirtyeight.com article, liveblog, project, video, podcast, and
	methodology page preserved by the Internet Archive. {total.toLocaleString()} entries.
	<a href="{base}/data/articles.csv">Download as CSV →</a>
</p>
