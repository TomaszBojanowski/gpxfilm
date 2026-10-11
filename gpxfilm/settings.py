"""The settings of a film as one structure. A light module: the command, a window and the worker process all read it, and
it can be sent between processes (pickled, or as the plain values of to_dict)."""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _, system_language

PATHS = ("gpx", "photos", "label_file", "font", "logo", "music")   # the settings that name a file or a folder
CODECS = {"h264": "H.264", "hevc": "HEVC"}   # the codecs a film can be asked for (--codec), the first by default
ENCODER_CODEC = {"libx264": "h264", "h264_videotoolbox": "h264", "libopenh264": "h264", "libx265": "hevc",
                 "libsvtav1": "av1"}     # the ffmpeg encoders the program knows, the preferred of each codec first; AV1 only by name


def codec_clash(codec: str | None, encoder: str | None) -> str | None:
    """What is wrong when a codec and an encoder are both asked for and the encoder makes another codec, or None. An
    encoder asked for alone decides the codec, and one the program does not know is not judged."""
    own = ENCODER_CODEC.get(encoder or "")
    if codec and own and own != codec:
        return _("The encoder {encoder} does not make {codec} films: leave out --codec or --encoder, or make them agree.").format(
            encoder=encoder, codec=CODECS.get(codec, codec))
    return None


@dataclass(frozen=True)
class Settings:
    """Everything that decides what the film looks like. The names are those of the options of the command, with
    underscores; what the command should write and where (the film, a frame, a report, slides) is not a setting."""
    gpx: Path | None = None
    track: int | None = None             # the number of the track or route, when the file has several
    keep_times: bool = False
    title: str | None = None             # its lines separated with "\n" (a backslash and n) or a line break
    subtitle: str | None = None
    auto_title: bool = False
    language: str = field(default_factory=system_language)   # of the texts on the film
    timezone: str | None = None
    size: str = "3840x2160"
    fps: int = 60
    route_duration: float = 16.0
    color: str = "#E5202E"
    color_by: str | None = None          # slope, speed, heart-rate or elevation
    map: str = "terrain"                 # terrain, topo, aerial, satellite, or an address of tiles or of a WMS
    map_style: str = "natural"           # natural, night, light or gray
    sunlight: bool = False
    minimap: bool = False
    background_names: bool = False
    no_osm: bool = False
    places: int = 6
    peaks: int = 3
    label_style: str = "strong"          # plain, strong or badges
    label_size: float = 1.0
    name_language: str = ""
    rename: tuple[str, ...] = ()         # each "OLD=NEW"
    skip: tuple[str, ...] = ()
    label_file: Path | None = None
    km_markers: bool = False
    distance: float | None = None        # km from the watch
    ascent: float | None = None          # m from the watch
    counters: bool = False
    no_heart_rate: bool = False
    stop_minutes: float = 5.0
    intro: bool = False
    intro_duration: float = 6.5
    intro_peaks: int = 5
    intro_places: int = 4
    intro_radius: float = 20.0           # km
    intro_label_duration: float | None = None
    photos: Path | None = None
    photo_duration: float = 2.5
    photo_offset: float = 0.0
    photo_limit: int = 15
    outro: bool = False
    outro_duration: float = 5.0
    font: Path | None = None
    logo: Path | None = None
    music: Path | None = None
    crf: int = 16
    codec: str | None = None             # h264 or hevc; None: H.264, unless an encoder asked for by name makes another
    encoder: str | None = None

    @classmethod
    def from_options(cls, options) -> "Settings":
        """The settings in the parsed options of the command."""
        given = vars(options)
        return cls.from_dict({f.name: given[f.name] for f in dataclasses.fields(cls) if f.name in given})

    @classmethod
    def from_dict(cls, values: dict) -> "Settings":
        """The settings from plain values (as to_dict gives them); what is left out keeps its default."""
        values = dict(values)
        for name in PATHS:
            if values.get(name) is not None:
                values[name] = Path(values[name])
        for name in ("rename", "skip"):
            values[name] = tuple(values.get(name) or ())
        return cls(**values)

    def to_dict(self) -> dict:
        """The settings as plain values that JSON can carry: paths as text, the lists as lists."""
        values = dataclasses.asdict(self)
        for name in PATHS:
            if values[name] is not None:
                values[name] = str(values[name])
        values["rename"], values["skip"] = list(self.rename), list(self.skip)
        return values

    def problem(self) -> str | None:
        """What is wrong with a number that is out of its range, as a message naming the option, or None."""
        if self.distance is not None and not (math.isfinite(self.distance) and 0 < self.distance <= 100000):
            return _("--distance must be a number greater than zero (in km, at most 100000)")
        if self.ascent is not None and not (math.isfinite(self.ascent) and 0 <= self.ascent <= 1000000):
            return _("--ascent must be a non-negative number (in m, at most 1000000)")
        if self.intro_peaks < 0:
            return _("--intro-peaks must be a non-negative number")
        if self.intro_places < 0:
            return _("--intro-places must be a non-negative number")
        if not (math.isfinite(self.intro_radius) and 0 < self.intro_radius <= 100):
            return _("--intro-radius must be a number greater than zero (in km, at most 100)")
        if self.intro_label_duration is not None and not (math.isfinite(self.intro_label_duration) and 0.5 <= self.intro_label_duration <= 20):
            return _("--intro-label-duration must be a number from 0.5 to 20 (in seconds)")
        if not (math.isfinite(self.label_size) and 0.7 <= self.label_size <= 2.0):
            return _("--label-size must be a number from 0.7 to 2.0")
        return codec_clash(self.codec, self.encoder)
