"""CLI 入口：在默认浏览器打开桌面看板。

用法：
    python -m monitor.dashboard
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboard" / "index.html"


def main():
    print(f"正在打开看板：{DASHBOARD}")
    subprocess.run(["open", str(DASHBOARD)], check=False)


if __name__ == "__main__":
    main()
