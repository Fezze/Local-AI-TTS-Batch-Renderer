from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MarkdownIngestOptions:
    single_chapter: bool = False
    max_chapter_chars: int = 0


@dataclass(frozen=True, init=False)
class SourceLoadOptions:
    markdown: MarkdownIngestOptions = field(default_factory=MarkdownIngestOptions)

    def __init__(
        self,
        *,
        markdown: MarkdownIngestOptions | None = None,
        markdown_single_chapter: bool | None = None,
        markdown_max_chapter_chars: int | None = None,
    ) -> None:
        markdown_options = markdown or MarkdownIngestOptions()
        if markdown_single_chapter is not None or markdown_max_chapter_chars is not None:
            markdown_options = MarkdownIngestOptions(
                single_chapter=markdown_single_chapter if markdown_single_chapter is not None else markdown_options.single_chapter,
                max_chapter_chars=markdown_max_chapter_chars if markdown_max_chapter_chars is not None else markdown_options.max_chapter_chars,
            )
        object.__setattr__(self, "markdown", markdown_options)


__all__ = ["MarkdownIngestOptions", "SourceLoadOptions"]
