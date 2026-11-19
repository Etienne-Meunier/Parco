"""One virtual LaTeX source with every `\\input`/`\\include` inlined, mapped back to the files."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from parco.core.project import Project


@dataclass(frozen=True)
class Segment:
    """Virtual text [vstart, vend) is a verbatim copy of `file` from `fstart`."""

    vstart: int
    vend: int
    file: str
    fstart: int

    @property
    def fend(self) -> int:
        """Offset in the file just after the copied text."""
        return self.fstart + self.vend - self.vstart


@dataclass
class VirtualSource:
    """The inlined document and the map between virtual offsets and (file, offset)."""

    text: str
    segments: list[Segment] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)

    def line_of(self, vpos: int) -> tuple[str, int]:
        """(file, 1-based line) of a virtual offset."""
        file, offset = self.to_file(vpos)
        return file, self.files[file].count("\n", 0, offset) + 1

    def to_virtual_range(self, file: str, start: int, end: int) -> tuple[int, int] | None:
        """Virtual span of the file span [start, end), or None if it is not one contiguous copy."""
        for seg in self.segments:
            if seg.file == file and seg.fstart <= start and end <= seg.fend and start < seg.fend:
                return seg.vstart + start - seg.fstart, seg.vstart + end - seg.fstart
        return None

    def to_virtual(self, file: str, offset: int) -> int | None:
        """Virtual offset of a file offset (the first copy of that place)."""
        for seg in self.segments:
            if seg.file == file and seg.fstart <= offset <= seg.fend:
                return seg.vstart + offset - seg.fstart
        return None

    def segment_at(self, vpos: int) -> Segment:
        """The segment containing the virtual offset `vpos` (the last one at the very end)."""
        starts = [seg.vstart for seg in self.segments]
        index = max(bisect_right(starts, vpos) - 1, 0)
        return self.segments[index]

    def to_file(self, vpos: int) -> tuple[str, int]:
        """(file, offset) of a virtual offset."""
        seg = self.segment_at(vpos)
        return seg.file, seg.fstart + min(vpos, seg.vend) - seg.vstart


def build_virtual_source(project: Project) -> VirtualSource:
    """Inline the tracked files in reading order; a file included twice is only inlined once."""
    segments: list[Segment] = []
    parts: list[str] = []
    files: dict[str, str] = {}
    visited = {project.main_file()}

    def emit(file: str, text: str, fstart: int) -> None:
        if not text:
            return
        vstart = segments[-1].vend if segments else 0
        parts.append(text)
        segments.append(Segment(vstart, vstart + len(text), file, fstart))

    def inline(rel: str) -> None:
        text = files[rel] = project.read(rel)
        position = 0
        for include in project.includes(rel):
            emit(rel, text[position:include.start], position)
            if include.target not in visited:
                visited.add(include.target)
                inline(include.target)
            position = include.end
        emit(rel, text[position:], position)

    inline(project.main_file())
    return VirtualSource(text="".join(parts), segments=segments, files=files)
