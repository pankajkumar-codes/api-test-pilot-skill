import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "api-testing" / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))
FIXTURES = Path(__file__).parent / "fixtures"
