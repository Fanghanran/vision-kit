"""pytest 配置 — 将 src/ 加入 Python 路径"""

import sys
from pathlib import Path

# 将 src/ 目录加入 Python 路径，使 from sentinelmind.xxx import ... 生效
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
