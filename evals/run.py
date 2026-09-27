# -*- coding: utf-8 -*-
"""AntNest · 评测 CLI

    python -m evals.run                      # 跑全部用例
    python -m evals.run --category safety    # 只跑某一类
    python -m evals.run --version 1.4        # 指定版本号（决定结果文件名）
    python -m evals.run --save               # 落盘到 evals/results/<version>.json
    python -m evals.run --compare 1.3.1 1.4  # 对比两个版本的结果

**必须先设好环境再 import AntNest**：`antnest_config.py:118` 在 import 期发现没有
API Key 就 `sys.exit(1)`。harness._bootstrap_env 负责这件事，所以本模块不能
在文件顶层 import harness 之外的东西。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fmt_metrics(m: dict) -> list[str]:
    rows = [
        ("成功率", m.get("success_rate")),
        ("工具调用/用例", m.get("tool_calls_avg")),
        ("平均耗时 ms", m.get("duration_ms_avg")),
        ("最慢 ms", m.get("duration_ms_max")),
        ("token/用例", m.get("tokens_avg")),
        ("轮数/用例", m.get("rounds_avg")),
        ("错误恢复率", m.get("error_recovery_rate")),
        ("危险拦截率", m.get("danger_block_rate")),
    ]
    out = []
    for label, val in rows:
        if val is None:
            out.append(f"  {label:16s} -")
        else:
            out.append(f"  {label:16s} {val}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AntNest Agent 评测")
    ap.add_argument("--category", default="", help="只跑某一类（safety/planning/recovery/permissions）")
    ap.add_argument("--version", default="dev", help="版本号，决定结果文件名")
    ap.add_argument("--save", action="store_true", help="落盘结果")
    ap.add_argument("--verbose", action="store_true", help="逐用例打印")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"),
                    help="对比两个版本的结果文件")
    args = ap.parse_args(argv)

    # 必须在 import harness 之前 bootstrap（它自己会在 run() 里做，
    # 但 --compare 只需要纯文件操作，不该拉起 AntNest）
    if args.compare:
        from evals.harness import RESULTS_DIR, compare
        old = RESULTS_DIR / f"{args.compare[0]}.json"
        new = RESULTS_DIR / f"{args.compare[1]}.json"
        if not old.is_file() or not new.is_file():
            print(f"找不到结果文件：{old} / {new}", file=sys.stderr)
            print(f"请先跑 --version {args.compare[0]} 与 --version {args.compare[1]} --save",
                  file=sys.stderr)
            return 2
        print(json.dumps(compare(old, new), ensure_ascii=False, indent=2))
        return 0

    from evals.harness import load_cases, run, save_report

    cases = load_cases(args.category)
    if not cases:
        print(f"没有找到用例（category={args.category!r}）", file=sys.stderr)
        return 2

    print(f"AntNest 评测 · {len(cases)} 个用例 · category={args.category or 'all'}")
    print("（注意：假 LLM 驱动真实运行时，测的是**运行时管线**，不是模型能力）")
    print()
    report = run(cases=cases, version=args.version, verbose=args.verbose)

    for r in report.results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['case_id']:38s} {r['duration_ms']:8.0f}ms")
        for f in r["failures"]:
            print(f"        - {f}")
    print()
    print(f"通过 {report.passed}/{report.total}（{report.success_rate:.0%}）")
    for line in _fmt_metrics(report.metrics):
        print(line)

    if args.save:
        path = save_report(report)
        print(f"\n结果已写入 {path}")

    return 0 if report.passed == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
