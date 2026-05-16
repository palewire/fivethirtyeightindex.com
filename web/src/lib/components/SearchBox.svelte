<script lang="ts">
	interface Props {
		query: string;
		kindFilters: Map<string, boolean>;
		oninput: () => void;
	}

	let { query = $bindable(), kindFilters = $bindable(), oninput }: Props = $props();

	function toggle(kind: string) {
		kindFilters.set(kind, !kindFilters.get(kind));
		// Force reactivity — Svelte 5 maps are tracked by reference.
		kindFilters = new Map(kindFilters);
		oninput();
	}
</script>

<form class="search" onsubmit={(e) => e.preventDefault()}>
	<input
		type="search"
		placeholder="Search title or byline…"
		bind:value={query}
		{oninput}
		autocomplete="off"
		spellcheck="false"
	/>
	<div class="filters">
		{#each [...kindFilters.keys()] as kind (kind)}
			<label>
				<input
					type="checkbox"
					checked={kindFilters.get(kind)}
					onchange={() => toggle(kind)}
				/>{kind}
			</label>
		{/each}
	</div>
</form>
