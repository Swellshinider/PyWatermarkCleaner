import argparse
import os
from coordinates import Coord
from video_converter import VideoConverter

def main():
    parser = argparse.ArgumentParser(
        description="PyWatermarkCleaner - Remove or mask watermarks from videos using inpainting.\nby: Swellshinider"
    )
    parser.add_argument("-i", type=str, required=True, help="Specify the input video file.")
    parser.add_argument("--x", type=int, required=True, help="X-coordinate of rectangle's top-left corner.")
    parser.add_argument("--y", type=int, required=True, help="Y-coordinate of rectangle's top-left corner.")
    parser.add_argument("--width", type=int, required=True, help="Width of the rectangle.")
    parser.add_argument("--height", type=int, required=True, help="Height of the rectangle.")
    parser.add_argument("--preview", action="store_true", help="Enable preview mode.")

    args = parser.parse_args()
    
    watermark_coordinates = Coord(
        x=args.x,
        y=args.y,
        height=args.height,
        width=args.width
    )
    
    base_name, _ = os.path.splitext(os.path.basename(args.i))
    temp_video_path = f"temp_{base_name}.mp4"
    final_video_path = f"result_{base_name}.mp4"
    
    with VideoConverter(
        video_path=args.i,
        temp_video_path=temp_video_path,
        final_result_video_path=final_video_path,
        watermark_coordinates=watermark_coordinates,
        is_preview=args.preview
    ) as converter:
        for progress in converter.process():
            print(f"Processing: {progress:0.2f}%", end="\r")

    print("\nFinished!")

if __name__ == "__main__":
    main()
