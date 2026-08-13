"""Re-export render public API for tests and backward compatibility."""

from video_build.render.audio import (
    apply_loudnorm_two_pass,
    audio_mix_filter_parts,
    duck_linear_gain,
    duck_volume_expr,
    extra_speech_windows,
    is_music_bed,
    measure_loudness,
    mix_audio_tracks,
    speech_windows_from_edl,
    wants_duck,
)
from video_build.render.common import (
    SUB_FORCE_STYLE,
    SUB_STYLE_DEFAULTS,
    apply_caption_case,
    apply_transition_sugar,
    parse_picture,
    resolve_grade_filter,
    resolve_path,
    run,
    video_fade_filter,
)
from video_build.render.extract import (
    concat_segments,
    extract_all_segments,
    extract_segment,
    kenburns_filter,
)
from video_build.render.overlays import (
    build_final_composite,
    build_overlay_filters,
    overlay_input_args,
)
from video_build.render.probe import is_hdr_source, is_portrait_source
from video_build.render.subtitles import (
    build_master_srt,
    coerce_subtitle_style,
    force_style_from_edl,
)

__all__ = [
    "SUB_FORCE_STYLE",
    "SUB_STYLE_DEFAULTS",
    "apply_caption_case",
    "apply_loudnorm_two_pass",
    "apply_transition_sugar",
    "audio_mix_filter_parts",
    "build_final_composite",
    "build_master_srt",
    "build_overlay_filters",
    "coerce_subtitle_style",
    "concat_segments",
    "duck_linear_gain",
    "duck_volume_expr",
    "extra_speech_windows",
    "extract_all_segments",
    "extract_segment",
    "force_style_from_edl",
    "is_hdr_source",
    "is_music_bed",
    "is_portrait_source",
    "kenburns_filter",
    "measure_loudness",
    "mix_audio_tracks",
    "overlay_input_args",
    "parse_picture",
    "resolve_grade_filter",
    "resolve_path",
    "run",
    "speech_windows_from_edl",
    "video_fade_filter",
    "wants_duck",
]
