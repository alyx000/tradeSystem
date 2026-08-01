"""pattern-scan launchd 部署回归：包装脚本 5 条必须项 + plist 契约。

依据 `.agents/rules/launchd-deploy.md`。launchd 的失败模式多为静默（PATH 缺失、
env 未 source、脚本无执行位），这些只能靠文件内容断言在 CI 里兜住。
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "deploy/launchd/pattern-scan-runner.sh"
PLIST = REPO_ROOT / "deploy/launchd/com.alyx.tradesystem.pattern-scan.plist"


class TestRunner:
    def test_exists_and_executable(self):
        assert RUNNER.exists()
        assert os.access(RUNNER, os.X_OK), "runner 必须有执行位，否则 launchd 静默失败"

    def test_sets_path(self):
        assert 'export PATH="$HOME/.local/bin:' in RUNNER.read_text(encoding="utf-8")

    def test_cd_repo_root(self):
        assert 'cd "$REPO_ROOT"' in RUNNER.read_text(encoding="utf-8")

    def test_sources_env(self):
        text = RUNNER.read_text(encoding="utf-8")
        assert 'source "$HOME/.config/tradeSystem.env"' in text
        assert 'source "$REPO_ROOT/scripts/.env"' in text

    def test_env_exported_to_child_process(self):
        """两个 env 文件必须包在 `set -a` / `set +a` 之间。

        scripts/.env 是**裸** `KEY=value`（无 export），不加 set -a 时 wrapper 自己看得到
        但 `exec` 的 python 子进程 os.getenv 读不到——实测 env -i 干净环境下
        wrapper `TUSHARE_TOKEN=set` 而子进程 MISSING。功能目前由 main.py 的
        load_dotenv 兜住，但下面的 `[env] ...=set` 诊断会因此误导排障者。
        """
        text = RUNNER.read_text(encoding="utf-8")
        start, end = text.index("set -a"), text.index("set +a")
        assert start < text.index('source "$REPO_ROOT/scripts/.env"') < end
        assert start < text.index('source "$HOME/.config/tradeSystem.env"') < end

    def test_timestamped_start_line(self):
        assert "$(date '+%Y-%m-%d %H:%M:%S') pattern-scan daily start" in RUNNER.read_text(encoding="utf-8")

    def test_credential_diagnostic_does_not_leak_values(self):
        """凭据诊断必须用 ${VAR:+set} 只判存在。

        直接 echo "$TOKEN" 会把凭据写进 /tmp/*.log（本机任何用户可读）。
        """
        text = RUNNER.read_text(encoding="utf-8")
        assert "DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set}" in text
        assert "TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}" in text
        for var in ("DINGTALK_WEBHOOK_TOKEN", "DINGTALK_WEBHOOK_SECRET", "TUSHARE_TOKEN"):
            assert f'echo "{var}=${var}"' not in text
            assert f"${{{var}:-" not in text, "不得用 :- 默认值展开，会打印真值"

    def test_uses_absolute_python(self):
        assert "exec /usr/bin/python3 scripts/main.py pattern-scan daily" in RUNNER.read_text(encoding="utf-8")


class TestPlist:
    def _load(self) -> dict:
        return plistlib.loads(PLIST.read_bytes())

    def test_valid_plist(self):
        assert self._load()["Label"] == "com.alyx.tradesystem.pattern-scan"

    def test_program_points_at_runner(self):
        assert self._load()["ProgramArguments"] == [str(RUNNER)]

    def test_weekday_schedule_at_2245(self):
        """工作日 22:45；launchd 不支持 Weekday 范围语法，必须逐条列出。"""
        intervals = self._load()["StartCalendarInterval"]
        assert sorted(i["Weekday"] for i in intervals) == [1, 2, 3, 4, 5]
        assert {(i["Hour"], i["Minute"]) for i in intervals} == {(22, 45)}

    def test_no_run_at_load(self):
        """RunAtLoad 必须 false，否则每次 load 就推一次钉钉。"""
        assert self._load()["RunAtLoad"] is False

    def test_logs_merged_to_single_file(self):
        data = self._load()
        assert data["StandardOutPath"] == data["StandardErrorPath"] == "/tmp/tradesystem-pattern-scan.log"

    def test_sleep_policy_documented(self):
        assert re.search(r"Sleep policy:", PLIST.read_text(encoding="utf-8"))


def _load_via_plutil(path: Path) -> dict:
    """用 macOS 原生解析器读 plist。

    不能用 `plistlib`：它走 expat，严格拒绝 XML 注释内的 `--`（如 macro-flash.plist
    注释里的 `run --date`），而 launchd 用的正是 plutil 这套宽容解析器——
    以 plistlib 为准会把「launchd 能正常读」的文件误判为损坏。
    """
    out = subprocess.run(
        ["plutil", "-convert", "json", "-o", "-", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def test_schedule_does_not_collide_with_siblings():
    """22:45 必须与其它 per-task launchd 的触发点错开，避免并发打 Tushare。"""
    mine = (22, 45)
    checked = 0
    for path in sorted((REPO_ROOT / "deploy/launchd").glob("com.alyx.tradesystem.*.plist")):
        if path == PLIST:
            continue
        raw = _load_via_plutil(path).get("StartCalendarInterval")
        if raw is None:
            continue
        checked += 1
        for item in (raw if isinstance(raw, list) else [raw]):
            slot = (item.get("Hour"), item.get("Minute"))
            assert slot != mine, f"{path.name} 与 pattern-scan 同在 {mine}"
    assert checked > 0, "未读到任何兄弟 plist，冲突检查形同虚设"
