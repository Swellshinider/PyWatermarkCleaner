# PyWatermarkCleaner

**PyWatermarkCleaner** is a command-line tool that removes or masks watermarks from one or more videos using OpenCV’s inpainting technique. It leverages multi-threaded processing (up to a user-specified number of threads) for faster concurrent handling of multiple video files.

## Roadmap/Features

Here we have our features and the planned features for upcoming versions of PyWatermarkCleaner:

- [x] **Multiple Input Files**: Process multiple video files in one command. 
- [x] **Preview Mode**: Process only a small portion of each video to quickly confirm that the specified coordinates are correct.  
- [x] **Graceful Cancellation**: Press **Ctrl + C** to cancel ongoing processing. The tool checks a stop event to exit gracefully at the next possible opportunity.  
- [x] **Threaded Processing**: By default uses 4 worker threads, but you can override with `--thread`.  

- [ ] Support for Output Folder: Allow users to specify a custom directory where processed videos will be saved.
- [ ] Enhanced Logging: Provide detailed logs for each processing step.
- [ ] Customizable Inpainting Method: Allow users to choose between different inpainting algorithms.
- [ ] Video Format Conversion: Support converting videos to a different format after processing.
  
## Table of Contents

- [PyWatermarkCleaner](#pywatermarkcleaner)
  - [Roadmap/Features](#roadmapfeatures)
  - [Table of Contents](#table-of-contents)
  - [Requirements](#requirements)
  - [Installation](#installation)
  - [Usage](#usage)
    - [Required Arguments](#required-arguments)
    - [Optional Arguments](#optional-arguments)
    - [Usage Examples](#usage-examples)
  - [Preview Mode](#preview-mode)
  - [Cancellation](#cancellation)
  - [Project Structure](#project-structure)
  - [Contribution](#contribution)
    - [How to Contribute](#how-to-contribute)
  - [License](#license)

## Requirements

- **Python 3.12+**  
- **OpenCV** 
- **NumPy**  
- **FFmpeg** available in your system’s PATH (for audio reattachment)  

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/Swellshinider/PyWatermarkCleaner.git
   cd PyWatermarkCleaner
   ```
2. Install dependencies (e.g., OpenCV, NumPy):
   ```bash
   pip install -r requirements.txt
   ```
3. Ensure **FFmpeg** is installed on your system and accessible via `ffmpeg` command.

## Usage

Run the script with:
```bash
python main.py -i video1.mp4 video2.mp4 ... --x X_COORD --y Y_COORD --width W_VALUE --height H_VALUE [options]
```

### Required Arguments

- **`-i`** (one or more files):  
  Paths to the input video files.  
  Example: `-i video1.mp4 video2.mp4 "path\to\video\vid.mp4"`.

- **`--x`**:  
  X-coordinate of the rectangle’s top-left corner. Negative values mean “count from the right”.

- **`--y`**:  
  Y-coordinate of the rectangle’s top-left corner. Negative values mean “count from the bottom”.

- **`--width`**:  
  The width of the rectangle.

- **`--height`**:  
  The height of the rectangle.

### Optional Arguments

- **`--thread`**:  
  Number of threads (worker pool size) to process videos in parallel. Default: 4.

- **`--preview`**:  
  If specified, each video processes only ~2% of its frames. Useful for quickly verifying your watermark coordinates.

### Usage Examples

1. **Process a single file**:
   ```bash
   python main.py \
       -i "path/to/video.mp4" \
       --x 10 --y 20 --width 50 --height 100
   ```

2. **Process multiple files** in parallel:
    ```bash
    python main.py \
        -i video1.mp4 "path\to\video\video2.mp4" anotherVideo.avi \
        --x 0 --y -50 --width 100 --height 50
        --thread 4
    ```
    This will process all three videos concurrently (up to 4 threads).
    
Running will produce two files in the current directory:
- `temp_{video_name}.mp4` (temporary inpainted video)
- `result_{video_name}.mp4` (final video, audio reattached)

## Preview Mode

When you use the `--preview` flag, only about **2%** of each video is processed. This is particularly handy if your watermark coordinates might not be correct and you want to quickly test the region. Once you confirm your rectangle is correct, rerun **without** `--preview` for the final result.

## Cancellation

To **cancel** at any time, press **Ctrl + C**.  
- A **stop event** is set internally.  
- Each running thread checks this event periodically and exits gracefully soon after.

The partially processed `temp_*.mp4` files may remain, but the final `result_*.mp4` won’t be generated for any canceled task.

## Project Structure

```bash
PyWatermarkCleaner/
├── main.py               # Entry point (CLI)
├── video_converter.py    # Contains VideoConverter class
├── coordinates.py        # Defines Coord class for watermark coordinates
├── requirements.txt      # Lists Python dependencies
└── README.md             # You are here!
```

## Contribution

Contributions are welcome! If you have ideas to improve PyWatermarkCleaner, feel free to fork the repository, make your changes, and submit a pull request.

### How to Contribute

- Fork the repository.
- Create a new branch for your feature or bugfix.
- Please, test your changes before committing.
- Submit a pull request with a detailed explanation of your changes.
- :)

## License

This project is licensed under the MIT License. See the LICENSE file for details.

---

Enjoy using **PyWatermarkCleaner** to remove or mask watermarks from your videos quickly and easily!  

**By:** [Swellshinider](https://github.com/Swellshinider).