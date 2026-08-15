"""Media production layer (T-041).

Three modules, one responsibility each:

  ffmpeg   the boundary — probing, execution, platform quirks, structured errors
  edl      the document — what an edit IS, independent of how it renders
  render   the compiler — EDL plus resolved sources to an ffmpeg filter graph

The split matters. ``edl`` has no knowledge of ffmpeg, so an edit can be
created, validated, versioned and reviewed on a machine with no media tooling
installed at all; and ``render`` is a pure function producing a command line,
so the graph can be tested without encoding anything.
"""

from . import edl, ffmpeg, render  # noqa: F401

__all__ = ["edl", "ffmpeg", "render"]
