"""Default application settings. Override with create_app(test_config) or environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()  # .env: ANTHROPIC_API_KEY, ANTHROPIC_MODEL, TURNPILOT_SECRET_KEY


class DefaultConfig:
    SECRET_KEY = os.environ.get("TURNPILOT_SECRET_KEY", "dev")

    # --- drawing upload ---------------------------------------------------
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # bytes; larger uploads get HTTP 413
    ALLOWED_DRAWING_EXTENSIONS = ("png", "jpg", "jpeg", "pdf")

    # --- Claude vision ----------------------------------------------------
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    # "features" (default) or the "dimensions_first" experiment, see turnpilot/dimensions_first.py
    DRAWING_READ_MODE = os.environ.get("TURNPILOT_READ_MODE", "features")
    # Features below this confidence are highlighted on the review screen.
    LOW_CONFIDENCE_THRESHOLD = 0.7

    # --- blank suggestion (used when the drawing has no blank) ------------
    # PLACEHOLDER: typical round bar stock diameters, mm. Replace with the supplier's list.
    BAR_STOCK_DIAMETERS = (
        10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 35, 36, 38, 40, 42, 45, 48, 50,
        55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 110, 120, 130, 140, 150, 160, 180, 200,
    )
    BLANK_DIAMETER_ALLOWANCE_MM = 2.0  # added to the largest diameter before rounding up to bar
    BLANK_FACING_ALLOWANCE_MM = 2.0  # total for both faces
    DEFAULT_PARTING_WIDTH_MM = 3.0  # used when no parting tool with a width is in the turret
