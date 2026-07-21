from pathlib import Path

import pytest

from pywatermarkcleaner.core.output_paths import allocate_output_path


def test_first_output_choice_preserves_source_suffix_without_creating_file(
    tmp_path: Path,
) -> None:
    result = allocate_output_path(Path("clip.mov"), tmp_path)

    assert result == tmp_path / "clip_cleaned.mov"
    assert not result.exists()


def test_existing_output_names_increment_without_overwriting(tmp_path: Path) -> None:
    (tmp_path / "clip_cleaned.mkv").touch()
    (tmp_path / "clip_cleaned_2.mkv").touch()

    result = allocate_output_path(Path("clip.mkv"), tmp_path)

    assert result == tmp_path / "clip_cleaned_3.mkv"
    assert not result.exists()


def test_multi_dot_input_keeps_all_stem_parts(tmp_path: Path) -> None:
    result = allocate_output_path(Path("holiday.final.cut.webm"), tmp_path)

    assert result == tmp_path / "holiday.final.cut_cleaned.webm"


@pytest.mark.parametrize("extension", ["mp4", ".mp4"])
def test_explicit_mp4_extension_is_a_fallback_for_suffixless_input(
    tmp_path: Path, extension: str
) -> None:
    result = allocate_output_path(Path("recording"), tmp_path, extension)

    assert result == tmp_path / "recording_cleaned.mp4"
