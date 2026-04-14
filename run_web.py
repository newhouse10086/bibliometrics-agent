#!/usr/bin/env python
"""
启动 Bibliometrics Agent Web 界面

运行此脚本以启动 Web 服务器，然后访问 http://localhost:8000
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from web_api import app
import uvicorn

if __name__ == "__main__":
    print("=" * 70)
    print("Bibliometrics Agent Web Interface")
    print("=" * 70)
    print("\n启动服务器...")
    print("访问地址: http://localhost:8001")
    print("\n按 Ctrl+C 停止服务器\n")
    print("=" * 70)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info"
    )
