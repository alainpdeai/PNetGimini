import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .masking import MaskingFilter

def setup_logger(log_dir: Path):
    """Configure and initialize logging system, integrating credential masking filter."""
    log_file_path = log_dir / "automation.log"
    log_file_path.parent.mkdir(exist_ok=True)

    # Root logger initialization
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers
    if not logger.handlers:
        masking_filter = MaskingFilter()

        # Rotating file handler (10MB, up to 5 backups)
        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.INFO)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        file_handler.addFilter(masking_filter)
        logger.addHandler(file_handler)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        console_handler.addFilter(masking_filter)
        logger.addHandler(console_handler)