"""gpxfilm: an MP4 film of a GPX track on a shaded relief, topographic or aerial map."""
try:
    from ._version import version as __version__      # written from the git history when the package is built
except ImportError:                                   # a source tree that was never built or installed
    __version__ = "0+unknown"
