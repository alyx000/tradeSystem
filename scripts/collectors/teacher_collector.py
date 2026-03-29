#!/usr/bin/env python3
"""
老师观点采集器

复用现有 obsidian-review-storage 技能，将老师观点录入 tradeSystem 并同步到 Obsidian。

用法:
    # 录入老师观点
    python teacher_collector.py add --teacher 边风炜 --title "医药财报点评" --content "核心观点..."

    # 查询老师观点
    python teacher_collector.py list --teacher 边风炜

    # 导出到 tracking 文件
    python teacher_collector.py export
"""
import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = Path("/root/.openclaw/workspace/skills/obsidian-review-storage")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("teacher_collector")


class TeacherCollector:
    """老师观点采集器"""

    def __init__(self, tradesystem_dir: str = None):
        self.ts_dir = Path(tradesystem_dir) if tradesystem_dir else BASE_DIR
        self.tracking_file = self.ts_dir / "tracking" / "teacher-notes.yaml"
        self.store_script = SKILLS_DIR / "scripts" / "store_review.py"
        logger.info(f"tradeSystem 目录：{self.ts_dir}")

    def add_note(self, teacher: str, title: str, content: str, tags: list = None) -> dict:
        """
        添加老师观点（调用 obsidian-review-storage 技能）

        Args:
            teacher: 老师名称
            title: 观点标题
            content: 观点内容
            tags: 额外标签

        Returns:
            存储结果
        """
        logger.info(f"录入老师观点：{teacher} - {title}")

        # 构建命令行参数
        cmd = [
            sys.executable,
            str(self.store_script),
            "--type", "teacher",
            "--title", title,
            "--content", content,
            "--teacher", teacher,
        ]

        if tags:
            cmd.extend(["--tags"] + tags)

        # 执行命令
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

        if result.returncode == 0:
            output = json.loads(result.stdout)
            logger.info(f"存储成功：{output}")

            # 同时更新 tracking 文件
            self._update_tracking(teacher, title, output.get("path", ""))

            return output
        else:
            logger.error(f"存储失败：{result.stderr}")
            return {"status": "error", "message": result.stderr}

    def _update_tracking(self, teacher: str, title: str, obsidian_path: str):
        """更新 tracking/teacher-notes.yaml"""
        self.tracking_file.parent.mkdir(parents=True, exist_ok=True)

        # 读取现有数据
        tracking_data = {"teachers": []}
        if self.tracking_file.exists():
            with open(self.tracking_file, "r", encoding="utf-8") as f:
                tracking_data = yaml.safe_load(f) or tracking_data

        # 查找或创建老师记录
        teacher_record = None
        for t in tracking_data["teachers"]:
            if t.get("name") == teacher:
                teacher_record = t
                break

        if not teacher_record:
            teacher_record = {
                "name": teacher,
                "platform": "",
                "schedule": "",
                "last_update": datetime.now().strftime("%Y-%m-%d"),
                "notes": [],
            }
            tracking_data["teachers"].append(teacher_record)

        # 添加新笔记
        note = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "title": title,
            "obsidian_path": obsidian_path,
        }
        teacher_record["notes"].append(note)
        teacher_record["last_update"] = datetime.now().strftime("%Y-%m-%d")

        # 保存
        with open(self.tracking_file, "w", encoding="utf-8") as f:
            yaml.dump(tracking_data, f, allow_unicode=True, default_flow_style=False)

        logger.info(f"已更新 tracking 文件：{self.tracking_file}")

    def list_notes(self, teacher: str = None, limit: int = 10) -> list:
        """
        查询老师观点

        Args:
            teacher: 老师名称（可选，不填则返回所有）
            limit: 返回数量限制

        Returns:
            笔记列表
        """
        if not self.tracking_file.exists():
            logger.warning("tracking 文件不存在")
            return []

        with open(self.tracking_file, "r", encoding="utf-8") as f:
            tracking_data = yaml.safe_load(f) or {"teachers": []}

        results = []
        for t in tracking_data.get("teachers", []):
            if teacher and t.get("name") != teacher:
                continue

            notes = t.get("notes", [])[-limit:]
            results.append({
                "name": t["name"],
                "platform": t.get("platform", ""),
                "notes": notes,
            })

        return results

    def export(self) -> str:
        """
        导出 tracking 文件到 Obsidian

        Returns:
            导出的 Markdown 文件路径
        """
        if not self.tracking_file.exists():
            logger.warning("tracking 文件不存在")
            return None

        with open(self.tracking_file, "r", encoding="utf-8") as f:
            tracking_data = yaml.safe_load(f) or {"teachers": []}

        # 生成 Markdown
        md_lines = [
            "# 老师观点跟踪",
            f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "标签：#teacher-notes #跟踪",
            "",
        ]

        for t in tracking_data.get("teachers", []):
            md_lines.append(f"## {t['name']}")
            if t.get("platform"):
                md_lines.append(f"- **平台**: {t['platform']}")
            if t.get("schedule"):
                md_lines.append(f"- **更新时间**: {t['schedule']}")
            md_lines.append(f"- **最后更新**: {t.get('last_update', 'N/A')}")
            md_lines.append("")

            notes = t.get("notes", [])[-20:]  # 最近 20 条
            if notes:
                md_lines.append("### 最近观点")
                for n in reversed(notes):
                    md_lines.append(f"- **{n['date']}**: {n['title']}")
                    md_lines.append(f"  - 路径：{n.get('obsidian_path', 'N/A')}")
            md_lines.append("")

        # 写入 Obsidian
        ob_dir = Path("/root/.openclaw/workspace/obsidian-vault/trading")
        ob_dir.mkdir(parents=True, exist_ok=True)
        md_path = ob_dir / "teacher-tracking.md"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        logger.info(f"已导出跟踪文件：{md_path}")
        return str(md_path)


def main():
    parser = argparse.ArgumentParser(description="老师观点采集器")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # add 命令
    add_parser = subparsers.add_parser("add", help="添加老师观点")
    add_parser.add_argument("--teacher", required=True, help="老师名称")
    add_parser.add_argument("--title", required=True, help="观点标题")
    add_parser.add_argument("--content", required=True, help="观点内容")
    add_parser.add_argument("--tags", nargs="+", help="额外标签")

    # list 命令
    list_parser = subparsers.add_parser("list", help="查询老师观点")
    list_parser.add_argument("--teacher", help="老师名称（可选）")
    list_parser.add_argument("--limit", type=int, default=10, help="返回数量限制")

    # export 命令
    subparsers.add_parser("export", help="导出 tracking 到 Obsidian")

    args = parser.parse_args()
    collector = TeacherCollector()

    if args.command == "add":
        result = collector.add_note(
            teacher=args.teacher,
            title=args.title,
            content=args.content,
            tags=args.tags,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == "list":
        results = collector.list_notes(teacher=args.teacher, limit=args.limit)
        for r in results:
            print(f"\n{r['name']} ({r.get('platform', '')}):")
            for n in r.get("notes", []):
                print(f"  - {n['date']}: {n['title']}")

    elif args.command == "export":
        result = collector.export()
        if result:
            print(f"已导出：{result}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
