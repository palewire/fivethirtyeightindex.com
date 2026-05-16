<script lang="ts">
	import { base } from '$app/paths';

	interface Props {
		total: number;
	}
	let { total }: Props = $props();
</script>

<p class="tagline">
	Search the {total.toLocaleString()} fivethirtyeight.com pages preserved by the Internet Archive
</p>
