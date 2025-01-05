class Coord():
    """
    A class to represent and handle rectangular coordinates, 
    including the ability to normalize coordinates relative to a frame's dimensions.
    """
    def __init__(self, x: int, y: int, height: int, width: int):
        """
        Initializes a Coord instance with x, y, height, and width values.

        Args:
            x (int): The x-coordinate of the rectangle's top-left corner. Can be negative for relative positioning.
            y (int): The y-coordinate of the rectangle's top-left corner. Can be negative for relative positioning.
            height (int): The height of the rectangle.
            width (int): The width of the rectangle.
        """
        self.x = x
        self.y = y
        self.height = height
        self.width = width

    def retrieve_normalized_coordinates(self, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        """
        Normalizes the rectangle's coordinates relative to the frame dimensions.
        Adjusts negative x or y coordinates to their positive counterparts 
        based on the frame's width and height.

        Args:
            frame_width (int): The width of the video frame.
            frame_height (int): The height of the video frame.

        Returns:
            tuple[int, int, int, int]: A tuple containing the normalized x, y, width, and height values.
        """
        if (self.x < 0):
            self.x = frame_width + self.x

        if (self.y < 0):
            self.y = frame_height + self.y

        return (self.x, self.y, self.width, self.height)