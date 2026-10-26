# Worker2 历史归档盘位迁移

**状态：ARCHIVE_RELOCATION_VERIFIED。** 在五主机故障残留和竞赛单元均为空时，将 Worker2 的 `SF-009-ATTEMPT-01` 受保护历史归档从 `/data/rca-archives/ops-competition-2026/` 迁到同机 `/root/ops-competition-2026/relocated-archives/`，并在原路径建立 root 可读链接。Master 原始 Case、Worker3 副本、Public/Private 文件、GT 和比赛服务器均未修改。

源 raw Manifest SHA-256 为 `2810255d6839528c028aa1e810b94f0931d61b7353345e40fcffd15084e7568f`，包含 2,604 文件、5,694,685,340 B；outputs Manifest SHA-256 为 `f09f9dd549c9aa7becfc410c3356f64b42ae9cbc9c067516eba92f24108a6b92`，包含 9 文件、39,340,913 B。两个哈希与生产总账的 ATTEMPT-01 记录一致。

原始归档在旧位置、新位置及旧路径链接三次通过逐文件 SHA 和 Metrics/Logs/Traces 恢复读回；outputs 三次通过逐文件 SHA。审计文件位于 Worker2 `/root/ops-competition-2026/relocation-audit/SF-009-ATTEMPT-01-*`。迁移前 Worker2 系统盘与数据盘可用分别约 68,991,414,272 B、9,408,512,000 B；迁移后约 63,115,579,392 B、15,135,608,832 B，均高于十进制 5 GB。

该操作只改变历史归档的物理盘位，保留原访问路径。后续正式题仍须重新运行自己的完整五主机容量、健康、Watchdog 和业务准入；迁移本身不授予故障执行许可。
