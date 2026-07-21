"""Collision-safe output filename allocation without filesystem mutation."""

from pathlib import Path


def allocate_output_path(
    input_path: str | Path,
    output_dir: str | Path,
    extension: str | None = None,
) -> Path:
    """Return the first available cleaned filename in ``output_dir``."""
    source = Path(input_path)
    destination = Path(output_dir)
    selected_extension = (
        extension.lstrip(".") if extension is not None else source.suffix.lstrip(".")
    )
    if not selected_extension:
        raise ValueError("an output extension is required")

    base_name = f"{source.stem}_cleaned"
    candidate = destination / f"{base_name}.{selected_extension}"
    index = 2
    while candidate.exists():
        candidate = destination / f"{base_name}_{index}.{selected_extension}"
        index += 1
    return candidate
