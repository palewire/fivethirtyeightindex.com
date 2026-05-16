<script lang="ts">
	interface Props {
		value: string;
		placeholder?: string;
		oninput?: () => void;
	}

	let { value = $bindable(), placeholder = 'Search…', oninput }: Props = $props();
</script>

<form class="search" onsubmit={(e) => e.preventDefault()}>
	<input
		type="search"
		{placeholder}
		bind:value
		{oninput}
		autocomplete="off"
		spellcheck="false"
	/>
</form>
