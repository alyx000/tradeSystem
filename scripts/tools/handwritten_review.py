"""手写复盘独立档案：保留原文与事后补充，通过 CLI 记录请求者。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.handwritten_review import DEFAULT_ARCHIVE, amend_entry, build_reader, import_entry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-dir', type=Path, default=DEFAULT_ARCHIVE)
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('import', help='原子新增同日档案；拒绝覆盖旧档')
    add.add_argument('--date', required=True)
    add.add_argument('--source-file', type=Path, required=True)
    add.add_argument('--supplement-file', type=Path, required=True)
    add.add_argument('--input-by', required=True)
    amend = commands.add_parser('amend', help='保留原文及旧稿，追加有来源链的整理稿修订')
    amend.add_argument('--date', required=True)
    amend.add_argument('--supplement-file', type=Path, required=True)
    amend.add_argument('--expected-sha256', required=True)
    amend.add_argument('--reason', required=True)
    amend.add_argument('--input-by', required=True)
    build = commands.add_parser('build', help='校验档案并重建独立 HTML 阅读器')
    build.add_argument('--input-by', required=True)
    args = parser.parse_args(argv)
    if not args.input_by.strip():
        parser.error('--input-by 不得为空')
    try:
        if args.command == 'import':
            result = import_entry(args.archive_dir, args.date, args.source_file, args.supplement_file, args.input_by)
        elif args.command == 'amend':
            result = amend_entry(args.archive_dir, args.date, args.supplement_file,
                                 args.expected_sha256, args.reason, args.input_by)
        else:
            result = build_reader(args.archive_dir, args.input_by)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status='source_failed', error=str(exc)), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('saved', 'already_saved', 'complete') else 2


if __name__ == '__main__':
    raise SystemExit(main())
