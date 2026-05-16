<script lang="ts">
	interface Props {
		value: string;
		placeholder?: string;
		oninput?: () => void;
	}

	let { value = $bindable(), placeholder = 'Search…', oninput }: Props = $props();
</script>

<form onsubmit={(e) => e.preventDefault()}>
	<input
		type="search"
		{placeholder}
		bind:value
		{oninput}
		autocomplete="off"
		spellcheck="false"
	/>
</form>

<style>
	form {
		margin-bottom: var(--space-lg);
	}

	input[type='search'] {
		width: 100%;
		max-width: 32rem;
		padding: var(--space-sm) 0.5em;
		font: inherit;
		border: var(--rule-thin);
		background: var(--color-bg);
	}

	input[type='search']:focus {
		outline: none;
		border-color: var(--color-fg);
	}
</style>
