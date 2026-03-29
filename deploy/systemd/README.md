# systemd 示例（VPS）

将路径中的 `PATH_TO_TRADESYSTEM` 替换为仓库根目录（例如 `/root/tradeSystem`），复制到 `/etc/systemd/system/`，然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trade-pre.timer trade-post.timer
```

- **trade-pre**：工作日 07:00，`main.py pre`
- **trade-post**：工作日 20:00，`scripts/sync_data.sh`（内含 `post`，已含原 evening 流程）

若 `OnCalendar` 需指定时区，在较新 systemd 中可写：`OnCalendar=Mon..Fri 07:00:00 Asia/Shanghai`
