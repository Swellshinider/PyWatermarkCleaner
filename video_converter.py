import os
import threading
import cv2 as cv
import numpy as np
from typing import Any, Generator
from coordinates import Coord


class VideoConverter():
    """
    A class to handle video processing tasks, such as removing watermarks.
    Supports context management for efficient resource handling.
    """
    def __init__(self, video_path: str, temp_video_path: str, final_result_video_path: str, watermark_coordinates: Coord, is_preview: bool = False):
        """
        Initializes the VideoConverter with the video file path, temporary output file path,
        watermark coordinates, and a preview flag.

        Args:
            video_path (str): Path to the input video file.
            temp_video_path (str): Temporary path to save the processed video.
            final_result_video_path (str): Path to the final result of the processed video.
            watermark_coordinates (Coord): Coordinates of the watermark in normalized format.
            is_preview (bool): If True, processes only a small portion of the video (2%).
        """
        self.video_path: str = video_path
        self.temp_video_path: str = temp_video_path
        self.final_result_video_path: str = final_result_video_path
        self.coordinates: Coord = watermark_coordinates
        self.preview: bool = is_preview
        
        if not isinstance(self.coordinates, Coord):
            raise TypeError("watermark_coordinates must be of type Coord")

        # Video attributes initialized to default values
        self.video_fps: int = 0
        self.video_frame_width: int = 0
        self.video_frame_height: int = 0
        self.video_total_frames: int = 0
        
        # Video capture and output objects, initialized as None
        self.video_capture: cv.VideoCapture = None
        self.video_output: cv.VideoWriter = None

    def __enter__(self):
        """
        Sets up the video capture and output objects.
        Called when entering the `with` block.

        Returns:
            VideoConverter: The current instance for use in the `with` block.
        """
        try:
            # Initialize the video capture object
            self.video_capture = cv.VideoCapture(self.video_path)
            
            if not self.video_capture.isOpened():
                raise FileNotFoundError(f"Unable to open video file: {self.video_path}")
            
            # Retrieve video properties
            self.video_fps = self.video_capture.get(cv.CAP_PROP_FPS)
            self.video_total_frames = int(self.video_capture.get(cv.CAP_PROP_FRAME_COUNT))
            self.video_frame_width = int(self.video_capture.get(cv.CAP_PROP_FRAME_WIDTH))
            self.video_frame_height = int(self.video_capture.get(cv.CAP_PROP_FRAME_HEIGHT))
            
            # Initialize the video writer object for saving processed video
            self.video_output = cv.VideoWriter(
                self.temp_video_path,
                cv.VideoWriter_fourcc(*'mp4v'),
                self.video_fps,
                (self.video_frame_width, self.video_frame_height)
            )
            
            return self
        except Exception as e:
            if self.video_capture is not None:
                self.video_capture.release()
            raise e
 
    def __exit__(self, exc_type, exc_value, traceback):
        """
        Cleans up resources when exiting the `with` block.
        """
        if self.video_capture is not None:
            self.video_capture.release()
            
        if self.video_output is not None:
            self.video_output.release()
            
        if os.path.exists(self.temp_video_path) and not self.preview:
            try:
               os.remove(self.temp_video_path)
            except Exception as e:
               print(f"Failed to delete temporary file: {e}")
            
        if exc_type is not None:
            print(f"Exception occurred: {exc_value}")
            
        return False

    def process(self, stop_event: threading.Event = None) -> Generator[float, Any, None]:
        """
        Processes the video by removing the watermark using the provided coordinates.

        Yields:
            float: Progress percentage of the video processing.
        """
        wm_x, wm_y, wm_w, wm_h = self.coordinates.retrieve_normalized_coordinates(self.video_frame_width, self.video_frame_height)

        current_frame = 0
        frames_to_process = int(self.video_total_frames * 0.02) if self.preview else self.video_total_frames

        # Process each frame
        while True:
            if stop_event and stop_event.is_set():
                break
            
            ret, frame = self.video_capture.read()
            
            # Stop if no more frames or preview limit is reached
            if not ret or current_frame >= frames_to_process:
                break
            
            # Create a mask for the watermark region
            mask = np.zeros(frame.shape[:2], np.uint8)
            mask[wm_y:wm_y+wm_h, wm_x:wm_x+wm_w] = 255

            inpainted_frame = cv.inpaint(frame, mask, 3, cv.INPAINT_TELEA)
            self.video_output.write(inpainted_frame)

            current_frame += 1
            yield (current_frame / frames_to_process) * 100
        
        self.video_capture.release()
        self.video_output.release()
        
        if (not self.preview) and (not stop_event or not stop_event.is_set()):
            os.system(f'ffmpeg -i "{self.temp_video_path}" -i "{self.video_path}" -c copy -map 0:v:0 -map 1:a:0 "{self.final_result_video_path}" -hide_banner -loglevel error')