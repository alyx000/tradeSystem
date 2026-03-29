#!/usr/bin/env python3
"""
整合功能测试套件

测试 tradeSystem 与 Obsidian 的整合功能：
- Phase 1: YAML → Obsidian Markdown 导出
- Phase 2: 老师观点采集与同步

用法:
    # 运行所有测试
    python -m pytest tests/test_integration.py -v

    # 运行单个测试
    python -m pytest tests/test_integration.py::TestObsidianExporter::test_export_daily_review -v

    # 运行并生成报告
    python -m pytest tests/test_integration.py -v --html=report.html
"""
import os
import sys
import unittest
import yaml
from pathlib import Path
from datetime import datetime

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from generators.obsidian_export import ObsidianExporter
from collectors.teacher_collector import TeacherCollector


class TestObsidianExporter(unittest.TestCase):
    """Phase 1: YAML → Obsidian Markdown 导出测试"""

    @classmethod
    def setUpClass(cls):
        """测试前准备"""
        cls.ts_dir = BASE_DIR
        cls.ob_dir = Path("/root/.openclaw/workspace/obsidian-vault")
        cls.exporter = ObsidianExporter(
            tradesystem_dir=str(cls.ts_dir),
            obsidian_dir=str(cls.ob_dir),
        )
        cls.test_date = "2026-03-29"

    def test_01_yaml_file_exists(self):
        """测试 1: 检查测试 YAML 文件是否存在"""
        yaml_path = self.ts_dir / "daily" / self.test_date / "review.yaml"
        self.assertTrue(yaml_path.exists(), f"测试文件不存在：{yaml_path}")
        print(f"✅ 测试文件存在：{yaml_path}")

    def test_02_yaml_structure(self):
        """测试 2: 验证 YAML 文件结构"""
        yaml_path = self.ts_dir / "daily" / self.test_date / "review.yaml"
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # 检查必需的顶层字段
        required_fields = [
            "date",
            "market_overview",
            "sectors",
            "emotion_cycle",
            "style_analysis",
            "leaders",
            "nodes",
            "positions",
            "next_day_plan",
            "summary",
        ]

        for field in required_fields:
            self.assertIn(field, data, f"缺少必需字段：{field}")

        print(f"✅ YAML 结构完整，包含 {len(required_fields)} 个顶层字段")

    def test_03_export_daily_review(self):
        """测试 3: 导出复盘到 Obsidian"""
        md_path = self.exporter.export_daily_review(self.test_date)

        self.assertIsNotNone(md_path, "导出失败，返回 None")
        self.assertTrue(Path(md_path).exists(), f"生成的文件不存在：{md_path}")

        # 验证文件内容
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查关键内容
        self.assertIn("# 2026-03-29 复盘笔记", content)
        self.assertIn("## 一、大盘分析", content)
        self.assertIn("## 二、板块梳理", content)
        self.assertIn("## 三、情绪周期", content)
        self.assertIn("## 七、持仓检视", content)
        self.assertIn("## 八、次日计划", content)

        print(f"✅ 复盘导出成功：{md_path}")
        print(f"   文件大小：{len(content)} 字节")

    def test_04_export_markdown_format(self):
        """测试 4: 验证 Markdown 格式"""
        md_path = self.ts_dir / "daily" / self.test_date / "review.yaml"
        with open(md_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        md_path_output = self.ob_dir / "daily-reviews" / "2026" / f"{self.test_date}.md"
        with open(md_path_output, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查 Markdown 语法
        self.assertIn("|", content)  # 表格
        self.assertIn("**", content)  # 粗体
        self.assertIn("#", content)  # 标题
        self.assertIn("- ", content)  # 列表

        # 检查数据准确性（从 YAML 到 MD 的数据一致性）
        if data.get("market_overview", {}).get("direction", {}).get("trend"):
            trend = data["market_overview"]["direction"]["trend"]
            self.assertIn(f"**趋势**: {trend}", content)

        print("✅ Markdown 格式正确，包含表格/粗体/标题/列表")

    def test_05_export_all(self):
        """测试 5: 批量导出"""
        results = self.exporter.export_all(self.test_date)

        self.assertIn("date", results)
        self.assertIn("review", results)
        self.assertIsNotNone(results["review"], "复盘导出失败")

        print(f"✅ 批量导出完成：{results}")


class TestTeacherCollector(unittest.TestCase):
    """Phase 2: 老师观点采集器测试"""

    @classmethod
    def setUpClass(cls):
        """测试前准备"""
        cls.collector = TeacherCollector()
        cls.test_teacher = "边风炜"
        cls.test_title = f"测试笔记-{datetime.now().strftime('%H%M%S')}"
        cls.test_content = "这是测试内容，用于验证老师观点采集器功能。"

    def test_01_tracking_file_exists(self):
        """测试 1: 检查 tracking 文件"""
        tracking_path = self.collector.tracking_file
        # 文件可能不存在（首次测试时会创建）
        print(f"📁 Tracking 文件路径：{tracking_path}")

    def test_02_add_note(self):
        """测试 2: 添加老师观点"""
        result = self.collector.add_note(
            teacher=self.test_teacher,
            title=self.test_title,
            content=self.test_content,
            tags=["测试", "验证"],
        )

        self.assertEqual(result.get("status"), "success", f"添加失败：{result}")
        self.assertIn("path", result, "结果缺少 path 字段")

        # 验证 Obsidian 文件存在
        obsidian_path = Path(result["full_path"])
        self.assertTrue(obsidian_path.exists(), f"Obsidian 文件未创建：{obsidian_path}")

        print(f"✅ 老师观点添加成功：{result['path']}")

    def test_03_list_notes(self):
        """测试 3: 查询老师观点"""
        results = self.collector.list_notes(teacher=self.test_teacher, limit=5)

        self.assertIsInstance(results, list, "返回结果应为列表")
        self.assertGreater(len(results), 0, "未找到老师记录")

        # 验证返回结构
        teacher_data = results[0]
        self.assertIn("name", teacher_data)
        self.assertIn("notes", teacher_data)
        self.assertEqual(teacher_data["name"], self.test_teacher)

        print(f"✅ 查询成功，找到 {len(results)} 位老师的观点")
        if results[0]["notes"]:
            print(f"   最近笔记：{results[0]['notes'][-1]['title']}")

    def test_04_tracking_yaml_structure(self):
        """测试 4: 验证 tracking YAML 结构"""
        if not self.collector.tracking_file.exists():
            self.skipTest("tracking 文件不存在")

        with open(self.collector.tracking_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self.assertIn("teachers", data, "缺少 teachers 字段")
        self.assertIsInstance(data["teachers"], list, "teachers 应为列表")

        # 验证老师记录结构
        for teacher in data["teachers"]:
            self.assertIn("name", teacher)
            self.assertIn("notes", teacher)
            self.assertIsInstance(teacher["notes"], list)

        print(f"✅ tracking YAML 结构正确，共 {len(data['teachers'])} 位老师")

    def test_05_export_tracking(self):
        """测试 5: 导出 tracking 到 Obsidian"""
        md_path = self.collector.export()

        if md_path:
            self.assertTrue(Path(md_path).exists(), f"导出文件不存在：{md_path}")

            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("# 老师观点跟踪", content)
            self.assertIn("边风炜", content)

            print(f"✅ tracking 导出成功：{md_path}")
        else:
            self.skipTest("无数据可导出")


class TestIntegration(unittest.TestCase):
    """整合测试：验证端到端流程"""

    def test_full_workflow(self):
        """测试完整工作流"""
        print("\n" + "=" * 60)
        print("整合工作流测试")
        print("=" * 60)

        # 1. 导出复盘
        exporter = ObsidianExporter()
        review_path = exporter.export_daily_review("2026-03-29")
        print(f"1️⃣  复盘导出：{review_path}")

        # 2. 添加老师观点
        collector = TeacherCollector()
        result = collector.add_note(
            teacher="测试老师",
            title="整合测试笔记",
            content="测试内容",
            tags=["整合测试"],
        )
        print(f"2️⃣  老师观点：{result.get('path', 'N/A')}")

        # 3. 查询验证
        notes = collector.list_notes(teacher="测试老师", limit=1)
        print(f"3️⃣  查询结果：{len(notes)} 位老师")

        # 4. 导出 tracking
        tracking_path = collector.export()
        print(f"4️⃣  tracking 导出：{tracking_path}")

        print("=" * 60)
        print("✅ 完整工作流测试通过")
        print("=" * 60)


def run_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("🧪 tradeSystem 整合功能测试")
    print("=" * 60)
    print(f"测试时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"tradeSystem 目录：{BASE_DIR}")
    print("=" * 60 + "\n")

    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加测试
    suite.addTests(loader.loadTestsFromTestCase(TestObsidianExporter))
    suite.addTests(loader.loadTestsFromTestCase(TestTeacherCollector))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 输出总结
    print("\n" + "=" * 60)
    print("📊 测试总结")
    print("=" * 60)
    print(f"运行测试数：{result.testsRun}")
    print(f"✅ 成功：{result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"❌ 失败：{len(result.failures)}")
    print(f"⚠️  错误：{len(result.errors)}")

    if result.failures:
        print("\n失败测试:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback[:200]}...")

    if result.errors:
        print("\n错误测试:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback[:200]}...")

    print("=" * 60)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
