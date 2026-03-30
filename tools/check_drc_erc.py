#!/usr/bin/env python3
"""
DRC/ERC レポート検証スクリプト

KiBot が生成した DRC/ERC の JSON レポートを解析し、
重大度（severity）に基づいて CI の pass/fail を判定する。

使い方:
    python3 tools/check_drc_erc.py output/reports/

オプション:
    --exclusions <file>  除外設定ファイル（YAML/JSON）
    --warn-only          エラーがあっても exit 0（レポートのみ）
"""

import argparse
import json
import os
import sys
from pathlib import Path


def load_exclusions(exclusions_path):
    """除外設定を読み込む。未接続ピンなど意図的な違反を除外するために使用。

    除外設定ファイル (JSON) の例:
    {
        "excluded_types": ["silk_edge_clearance"],
        "excluded_descriptions": ["Pin unconnected.*NC"]
    }
    """
    if not exclusions_path or not os.path.exists(exclusions_path):
        return {"excluded_types": [], "excluded_descriptions": []}

    with open(exclusions_path) as f:
        return json.load(f)


def is_excluded(violation, exclusions):
    """違反が除外対象かどうかを判定する。"""
    import re

    vtype = violation.get("type", "")
    desc = violation.get("description", "")

    for excluded_type in exclusions.get("excluded_types", []):
        if vtype == excluded_type:
            return True

    for pattern in exclusions.get("excluded_descriptions", []):
        if re.search(pattern, desc):
            return True

    return False


def parse_report(report_path, exclusions):
    """DRC/ERC レポートを解析し、重大度別に違反を分類する。"""
    with open(report_path) as f:
        data = json.load(f)

    results = {
        "file": report_path,
        "errors": [],
        "warnings": [],
        "excluded": [],
    }

    violations = data.get("violations", [])

    for v in violations:
        if is_excluded(v, exclusions):
            results["excluded"].append(v)
            continue

        severity = v.get("severity", "error").lower()
        if severity == "warning":
            results["warnings"].append(v)
        else:
            # error, unspecified → エラー扱い
            results["errors"].append(v)

    return results


def format_violation(v, indent="  "):
    """違反を読みやすい形式でフォーマットする。"""
    lines = []
    vtype = v.get("type", "unknown")
    desc = v.get("description", "no description")
    lines.append(f"{indent}{vtype}: {desc}")

    # items がある場合は詳細を表示
    items = v.get("items", [])
    for item in items[:3]:  # 最大3件まで
        if isinstance(item, dict):
            item_desc = item.get("description", str(item))
            pos = item.get("pos", {})
            if pos:
                item_desc += f" @ ({pos.get('x', '?')}, {pos.get('y', '?')})"
            lines.append(f"{indent}  -> {item_desc}")

    return "\n".join(lines)


def print_summary(all_results):
    """全レポートのサマリーを出力する。"""
    total_errors = 0
    total_warnings = 0
    total_excluded = 0

    print("=" * 60)
    print("DRC/ERC Validation Report")
    print("=" * 60)

    for result in all_results:
        filename = os.path.basename(result["file"])
        errors = len(result["errors"])
        warnings = len(result["warnings"])
        excluded = len(result["excluded"])

        total_errors += errors
        total_warnings += warnings
        total_excluded += excluded

        # ステータスアイコン
        if errors > 0:
            icon = "FAIL"
        elif warnings > 0:
            icon = "WARN"
        else:
            icon = "PASS"

        print(f"\n[{icon}] {filename}")
        print(f"  Errors: {errors}, Warnings: {warnings}, Excluded: {excluded}")

        if result["errors"]:
            print(f"\n  --- Errors (CI will fail) ---")
            for v in result["errors"]:
                print(format_violation(v, "    "))

        if result["warnings"]:
            print(f"\n  --- Warnings ---")
            for v in result["warnings"][:10]:  # 最大10件
                print(format_violation(v, "    "))
            if len(result["warnings"]) > 10:
                print(f"    ... and {len(result['warnings']) - 10} more")

        if result["excluded"]:
            print(f"\n  --- Excluded ({excluded} items) ---")
            for v in result["excluded"][:3]:
                print(format_violation(v, "    "))
            if excluded > 3:
                print(f"    ... and {excluded - 3} more")

    print("\n" + "=" * 60)
    print(f"Total: {total_errors} error(s), {total_warnings} warning(s), {total_excluded} excluded")
    print("=" * 60)

    return total_errors, total_warnings, total_excluded


def generate_github_summary(all_results, total_errors, total_warnings, total_excluded):
    """GitHub Actions の Job Summary 用 Markdown を生成する。"""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    lines = []
    if total_errors > 0:
        lines.append("## :x: DRC/ERC Validation Failed")
    elif total_warnings > 0:
        lines.append("## :warning: DRC/ERC Validation Passed with Warnings")
    else:
        lines.append("## :white_check_mark: DRC/ERC Validation Passed")

    lines.append("")
    lines.append(f"| | Count |")
    lines.append(f"|---|---|")
    lines.append(f"| :red_circle: Errors | {total_errors} |")
    lines.append(f"| :yellow_circle: Warnings | {total_warnings} |")
    lines.append(f"| :heavy_minus_sign: Excluded | {total_excluded} |")
    lines.append("")

    for result in all_results:
        filename = os.path.basename(result["file"])
        errors = len(result["errors"])

        if errors > 0:
            lines.append(f"### :x: {filename}")
            lines.append("")
            lines.append("| Type | Description |")
            lines.append("|---|---|")
            for v in result["errors"][:20]:
                vtype = v.get("type", "unknown")
                desc = v.get("description", "").replace("|", "\\|")
                lines.append(f"| `{vtype}` | {desc} |")
            lines.append("")

    with open(summary_file, "a") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="DRC/ERC report validator")
    parser.add_argument("report_dir", help="Directory containing DRC/ERC JSON reports")
    parser.add_argument("--exclusions", help="Path to exclusions config file (JSON)")
    parser.add_argument("--warn-only", action="store_true",
                        help="Report only, do not fail CI on errors")
    args = parser.parse_args()

    report_dir = Path(args.report_dir)
    if not report_dir.exists():
        print(f"Warning: Report directory not found: {report_dir}")
        print("DRC/ERC reports may not have been generated.")
        sys.exit(0)

    # 除外設定を読み込む
    exclusions = load_exclusions(args.exclusions)

    # JSON レポートを検索
    report_files = sorted(report_dir.glob("*_report.json"))
    if not report_files:
        print(f"Warning: No report files found in {report_dir}")
        sys.exit(0)

    # 全レポートを解析
    all_results = []
    for report_file in report_files:
        try:
            result = parse_report(str(report_file), exclusions)
            all_results.append(result)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error parsing {report_file}: {e}")
            all_results.append({
                "file": str(report_file),
                "errors": [{"type": "parse_error", "description": str(e)}],
                "warnings": [],
                "excluded": [],
            })

    # サマリー出力
    total_errors, total_warnings, total_excluded = print_summary(all_results)

    # GitHub Actions Summary
    generate_github_summary(all_results, total_errors, total_warnings, total_excluded)

    # 終了コード
    if total_errors > 0 and not args.warn_only:
        print(f"\nCI FAILED: {total_errors} error(s) found.")
        print("Fix the errors, or add exclusions for intentional violations.")
        print("See: .drc-exclusions.json")
        sys.exit(1)
    elif total_errors > 0:
        print(f"\nWARN-ONLY mode: {total_errors} error(s) found but not failing CI.")
        sys.exit(0)
    else:
        print("\nCI PASSED.")
        sys.exit(0)


if __name__ == "__main__":
    main()
