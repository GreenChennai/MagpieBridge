"""息屏 CLI（独立于宿主使用）：python tools/screen_off.py off|on|status|toggle

- off    ：系统级息屏（关背光 + 保黑看门狗 + 吞没物理输入；ESC / Ctrl+Alt+O 唤醒）
- on     ：亮屏
- status ：查询当前状态
- toggle ：切换

7x24 挂机时用计划任务跑 `python tools/screen_off.py off` 即可息屏，
后台照常运行（显示器仍在线，微信不锁，合成输入不受影响）。
非 Windows 直接退出。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from magpie.core import screen_off  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"
    if cmd == "off":
        screen_off.turn_off_display()
    elif cmd == "on":
        screen_off.wake_display(reason="cli")
    elif cmd == "status":
        print("OFF (screen off)" if screen_off.is_screen_off() else "ON (screen on)")
    elif cmd == "toggle":
        print("OFF" if screen_off.toggle() else "ON")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
