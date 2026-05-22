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

export interface Dataset {
	id: string;
	slug: string;
	title: string;
	dataset_url: string;
	article_urls: string[];
	article_count: number;
	archive_url: string;
	date: string;
}

export interface Podcast {
	id: string;
	title: string;
	date: string;
	year: number | null;
	series: string;
	series_slug: string;
	url: string;
}
