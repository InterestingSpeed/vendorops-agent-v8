from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vendorops.seed import reset_database
print(f'Reset complete: {reset_database()}')
