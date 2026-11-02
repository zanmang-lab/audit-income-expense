"""pytest가 저장소 루트의 src/web 패키지를 찾게 한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
