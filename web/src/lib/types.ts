export type Kind =
	| 'article'
	| 'liveblog'
	| 'project'
	| 'podcast'
	| 'video'
	| 'methodology';

export interface Entry {
	id: string;
	title: string;
	byline: string;
	authors: string[];
	year: number | null;
	date: string;
	kind: Kind;
	url: string;
}
