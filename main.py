import os
import argparse
import signal
import threading
import traceback
from coordinates import Coord
from video_converter import VideoConverter
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait


stop_event = threading.Event()


def signal_handler(arg1, arg2):
    """
    Handle SIGINT (Ctrl + C). Set the stop_event,
    signaling all threads to stop gracefully.
    """
    print("\n[Main] Caught! Requesting all tasks to stop!")
    stop_event.set()
    

def process_video(input_path: str, watermark_coordinates: Coord, stop_event: threading.Event, only_one: bool, is_preview: bool = False):
    """
    Process a single video, removing the watermark.
    """
    try:
        base_name, _ = os.path.splitext(os.path.basename(input_path))
        current_thread = threading.current_thread()
        thread_id = current_thread.ident
        temp_video_path = f"temp_{base_name}.mp4"
        final_video_path = f"result_{base_name}.mp4"

        print(f"[Thread{thread_id}] Started processing '{input_path}'.")
        
        if (not os.path.exists(input_path)):
            raise FileNotFoundError

        with VideoConverter(
            video_path=input_path,
            temp_video_path=temp_video_path,
            final_result_video_path=final_video_path,
            watermark_coordinates=watermark_coordinates,
            is_preview=is_preview
        ) as converter:
            for pct in converter.process(stop_event):
                if only_one and current_thread.is_alive:
                    print(f"[Thread{thread_id}] Processing '{pct:2f}%'", end='\r')
                
        print()    
        return (input_path, temp_video_path, final_video_path, thread_id)
    except Exception:
        return (input_path, temp_video_path, final_video_path, thread_id)

def main():
    signal.signal(signal.SIGINT, signal_handler)
    parser = argparse.ArgumentParser(
        description="PyWatermarkCleaner - Remove or mask watermarks from videos using inpainting.\nby: Swellshinider"
    )
    parser.add_argument("-i", nargs="+", type=str, required=True, help="Specify the input video file.")
    parser.add_argument("--x", type=int, required=True, help="X-coordinate of rectangle's top-left corner.")
    parser.add_argument("--y", type=int, required=True, help="Y-coordinate of rectangle's top-left corner.")
    parser.add_argument("--width", type=int, required=True, help="Width of the rectangle.")
    parser.add_argument("--height", type=int, required=True, help="Height of the rectangle.")
    parser.add_argument("--thread", type=int, required=False, default=4, help="Maximum threads enabled to process multiple files.")
    parser.add_argument("--preview", action="store_true", help="Enable preview mode. Generate a little portion of the video, then you can check if your coordinates match.")

    args = parser.parse_args()
    max_workers = args.thread
    watermark_coordinates = Coord(
        x=args.x,
        y=args.y,
        height=args.height,
        width=args.width
    )
    
    intro = r"""
______      _    _       _                                 _    _____ _                            
| ___ \    | |  | |     | |                               | |  /  __ \ |                           
| |_/ /   _| |  | | __ _| |_ ___ _ __ _ __ ___   __ _ _ __| | _| /  \/ | ___  __ _ _ __   ___ _ __ 
|  __/ | | | |/\| |/ _` | __/ _ \ '__| '_ ` _ \ / _` | '__| |/ / |   | |/ _ \/ _` | '_ \ / _ \ '__|
| |  | |_| \  /\  / (_| | ||  __/ |  | | | | | | (_| | |  |   <| \__/\ |  __/ (_| | | | |  __/ |   
\_|   \__, |\/  \/ \__,_|\__\___|_|  |_| |_| |_|\__,_|_|  |_|\_\\____/_|\___|\__,_|_| |_|\___|_|   
       __/ |                                                                                       
      |___/                                                                                                                                                                                                                                                                         
by: Swellshinider
    """
    print(intro)
    print(f"Starting concurrent processing (up to {max_workers} threads)...\n")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        only_one: bool = len(args.i) == 1
        for input_path in args.i:
            futures.append(executor.submit(process_video, input_path, watermark_coordinates, stop_event, only_one, args.preview))

        try:
            
            while len(futures) > 0:
                done, _ = wait(futures, timeout=1, return_when=FIRST_EXCEPTION)
                
                if stop_event.is_set():
                    break
                
                thread_id: int = 0
                error_occurred: bool = False
                finished: bool = False
                video_current_path: str = ''
                video_temp_path: str = ''
                video_result_path: str = ''
                
                for fut in done:
                    futures.remove(fut)
                    try:
                        file_path, temp_path, result_path, id = fut.result()
                        video_current_path = file_path
                        video_temp_path = temp_path
                        video_result_path = result_path
                        thread_id = id
                        if stop_event.is_set():
                            break
                        finished = True
                    except FileNotFoundError:
                        finished = True
                        error_occurred = True
                    except Exception as e:
                        finished = True
                        error_occurred = True
                        print(f"[Thread{thread_id}] Unexpected error processing '{video_current_path}': {e}")
                        traceback.print_exception(type(e), e, e.__traceback__)
                 
                if (not error_occurred and finished):
                    print(f"\n[Thread{thread_id}] Finished processing '{video_current_path}'.")
                    print(f"         => Temp file : {video_temp_path}")
                    
                    if (not args.preview):
                        print(f"         => Final file: {video_result_path}\n")   
                    
        except Exception as e:
            print(f"[Main] Unexpected error: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
        finally:
            if stop_event.is_set():
                print("[Main] Stop event is set; waiting for threads to exit gracefully...")

    print(f"[Main] All tasks completed{(" in preview mode." if args.preview else ".")}")

if __name__ == "__main__":
    main()
