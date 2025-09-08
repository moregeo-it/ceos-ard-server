import logging
import subprocess

logger = logging.getLogger(__name__)


def check_ceos_ard_cli() -> bool:
    """Check if ceos-ard CLI is available."""
    try:
        result = subprocess.run(
            ["ceos-ard", "--version"],
            capture_output=True,
            timeout=10,  # 10 second timeout
        )
        success = result.returncode == 0
        if success:
            logger.info("ceos-ard CLI is available")
        else:
            logger.error("ceos-ard CLI check failed")
        return success

    except FileNotFoundError:
        logger.error("ceos-ard CLI is not installed or not available in the PATH")
        return False

    except subprocess.TimeoutExpired:
        logger.error("ceos-ard CLI check timed out")
        return False

    except Exception as e:
        logger.error(f"Error checking ceos-ard CLI: {e}")
        return False


# Check CLI availability once at module import (server startup)
CEOS_ARD_AVAILABLE = check_ceos_ard_cli()
