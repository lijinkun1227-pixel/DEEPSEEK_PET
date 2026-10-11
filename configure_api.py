"""一次性生成本地配置。把 API_KEY 填好后运行：python configure_api.py"""

from __future__ import annotations

import json
import getpass
from pathlib import Path
import os

# 只改这一行即可。不要把真实 key 提交到 git。
API_KEY = os.environ["DEEPSEEK_API_KEY"]
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "config.json"


def main() -> None:
    config = json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {}
    key = API_KEY.strip() or getpass.getpass("DeepSeek API key（输入隐藏，留空保留现有配置）：").strip()
    if key:
        config.update(api_key=key, base_url=BASE_URL.rstrip("/"), model=MODEL, api_enabled=True)
    TARGET.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    if key:
        print("API 文案已开启。重启桌宠后生效。")
    else:
        print("保留现有配置；没有 key 时使用本地模板。")


if __name__ == "__main__":
    main()
