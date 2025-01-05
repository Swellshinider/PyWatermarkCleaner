class Coord():
    def __init__(self, x: int, y: int, height: int, width: int):
        self.x = x
        self.y = y
        self.height = height
        self.width = width

    def retrieve_normalized_coordinates(self, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
        if (self.x < 0):
            self.x = frame_width + self.x

        if (self.y < 0):
            self.y = frame_height + self.y

        return (self.x, self.y, self.width, self.height)