"""盘中扫描本地HTML共用渲染，不涉及采集或发送。"""
from __future__ import annotations


def render_html_report(report: str, *, preview: bool = False) -> str:
    import markdown

    body = markdown.markdown(report, extensions=["tables", "md_in_html"])
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>盘中扫描新版预览</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172333;font:16px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1000px;margin:32px auto;padding:32px 42px;background:white;border-radius:16px;border:1px solid #e0e6ed}h3{font-size:26px;margin:0 0 20px}h4{font-size:20px;margin:32px 0 12px;border-top:1px solid #e0e6ed;padding-top:22px}main>p:first-of-type{background:#eaf1fa;border-left:4px solid #345f90;padding:16px 20px;font-size:19px}blockquote{margin:12px 0;color:#5b6878;font-size:14px;border-left:3px solid #d7dfe8;padding:0 14px;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{padding:10px;border-bottom:1px solid #dfe6ee}th{background:#f1f5fa}li{margin:5px 0;overflow-wrap:anywhere}.badge{color:#466581;font-size:13px;letter-spacing:1px;margin-bottom:14px}details{margin-top:24px}details{border:1px solid #e0e6ed;border-radius:8px;padding:0 14px}summary{cursor:pointer;font-weight:600;padding:12px 0}details[open]{padding-bottom:14px}details li{font-size:14px;overflow-wrap:anywhere}details h4{font-size:17px;margin-top:8px;padding-top:8px}@media(max-width:640px){main{margin:0;padding:22px 16px;border-radius:0}h3{font-size:22px}table{font-size:13px}th,td{padding:7px 3px}}
</style><main><div class="badge">__REPORT_BADGE__</div>'''
    document = document.replace("__REPORT_BADGE__", "本地预览 · 未推送" if preview else "盘中扫描 · 精简版")
    if not preview:
        document = document.replace("盘中扫描新版预览", "盘中扫描报告")
    return document + body + "</main></html>"
