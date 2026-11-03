"""发布入口与来源参数传递；不依赖真实业务库。"""
from tests.test_daily_review_html_report import _load_assembler


def test_write_report_propagates_both_theme_sources(tmp_path, monkeypatch):
    module = _load_assembler()
    launch, feedback = {'source': 'launch'}, {'source': 'feedback'}
    seen = {}
    def validate(html, **kwargs):
        seen.update(kwargs)
    monkeypatch.setattr(module, 'validate_report', validate)
    path = module.write_report('fixture', tmp_path/'report.html', capacity_manifest={},
                               new_high_manifest={}, index_intraday_manifest={},
                               theme_launch=launch, theme_feedback=feedback)
    assert path.read_text() == 'fixture'
    assert seen['theme_launch'] is launch and seen['theme_feedback'] is feedback
