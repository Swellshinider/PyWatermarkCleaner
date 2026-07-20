import subprocess
import sys


def test_numpy_and_opencv_import_in_supported_python() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import cv2, numpy"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
