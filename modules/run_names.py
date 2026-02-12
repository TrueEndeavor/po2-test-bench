"""
Generate cute random names for test runs with timestamps.
"""
import random
from datetime import datetime


CUTE_NAMES = [
    "Bubbles", "Sprinkles", "Cupcake", "Muffin", "Sparkle", "Twinkle", "Jellybean",
    "Buttons", "Peanut", "Cookie", "Waffles", "Pickles", "Nugget", "Skittles",
    "Snickers", "Pudding", "Marshmallow", "Biscuit", "Noodle", "Pumpkin", "Peaches",
    "Sunny", "Honey", "Coco", "Pepper", "Ginger", "Mocha", "Chai", "Latte",
    "Bamboo", "Blossom", "Clover", "Daisy", "Echo", "Frost", "Hazel", "Iris",
    "Luna", "Maple", "Nova", "Olive", "Pearl", "Rain", "River", "Sky",
    "Willow", "Ziggy", "Bingo", "Cosmo", "Dash", "Finn", "Milo", "Oscar",
    "Teddy", "Toby", "Yoshi", "Chip", "Doodle", "Fluffy", "Happy", "Lucky"
]


def generate_run_name():
    """
    Generate a cute random run name with timestamp.

    Format: cutename-YYYY-MM-DD-HH-MM-SS
    Example: Bubbles-2026-02-10-13-45-23
    """
    cute_name = random.choice(CUTE_NAMES)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    return f"{cute_name}-{timestamp}"


def parse_run_name(run_name):
    """
    Parse a run name to extract components.

    Returns:
        dict with name, timestamp, display_name
    """
    parts = run_name.split("-")

    if len(parts) >= 7:
        name = parts[0]
        timestamp_str = "-".join(parts[1:7])  # YYYY-MM-DD-HH-MM-SS

        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d-%H-%M-%S")

            return {
                "name": name,
                "timestamp": timestamp,
                "display_name": name,
                "timestamp_str": timestamp.strftime("%b %d, %Y %I:%M:%S %p"),
                "date_str": timestamp.strftime("%d %b"),
                "time_str": timestamp.strftime("%H:%M"),
            }
        except ValueError:
            pass

    # Fallback for unparseable names
    return {
        "name": run_name,
        "timestamp": None,
        "display_name": run_name,
        "timestamp_str": "",
        "date_str": "",
        "time_str": "",
    }
