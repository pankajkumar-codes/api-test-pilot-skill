import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "api-testing" / "scripts"))
FIXTURES = Path(__file__).parent / "fixtures"
