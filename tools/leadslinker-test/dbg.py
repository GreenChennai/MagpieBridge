# -*- coding: utf-8 -*-
"""通过调试通道向浏览器扩展下发调试命令并打印结果。

用法:
  python dbg.py snapshot
  python dbg.py find_text --json '{"text":"已留资","max":10}'
  python dbg.py test_selectors --json '{"selectors":["[class*=conversationItem]"]}'
  python dbg.py eval_code --code 'document.title'
  python dbg.py html --json '{"selector":".csUI-MessageItem","limit":5,"maxLen":3000}'
"""
import argparse
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:3000"


def query(cmd, params=None, timeout=30):
    body = json.dumps({
        "action": "debug_query",
        "params": {"cmd": cmd, "params": params or {}, "timeout": timeout},
    }).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/debug_query", data=body,
        headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=timeout + 60))
    d = r.get("data") or {}
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--json", dest="params", default="{}")
    ap.add_argument("--params-file", default=None, help="从文件读 params JSON(避开 shell 引号问题)")
    ap.add_argument("--code", default=None)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--out", default=None, help="结果保存文件(默认打印stdout)")
    args = ap.parse_args()

    if args.params_file:
        with open(args.params_file, encoding="utf-8") as f:
            params = json.load(f)
    else:
        params = json.loads(args.params)
    if args.code is not None:
        params["code"] = args.code

    d = query(args.cmd, params, args.timeout)
    if d.get("status") != "done":
        print("STATUS:", d.get("status"), "| cmd_id:", d.get("cmd_id"),
              "| hint:", d.get("hint") or d.get("error"))
        sys.exit(1)
    res = d.get("result")
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("saved ->", args.out, "| size", len(text))
    else:
        print(text)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
