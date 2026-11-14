"""Root pytest configuration and fixtures for agyflow."""

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
DEMO_REPO_DIR = REPO_ROOT / "tests" / "fixtures" / "demo_repo" / "src"

# Add src and demo_repo to sys.path for test discovery
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(DEMO_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_REPO_DIR))


@pytest.fixture
def staged_demo_repo(tmp_path: Path) -> Path:
    """Fixture staging an initialized git repository from demo_repo."""
    from tests.fixtures.demo_repo_helper import setup_demo_repo_git

    dest = tmp_path / "target_repo"
    return setup_demo_repo_git(dest)
