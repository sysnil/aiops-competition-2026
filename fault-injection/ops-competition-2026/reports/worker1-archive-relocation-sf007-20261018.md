# Worker1 SF-007 历史归档盘位迁移

**状态：`ARCHIVE_RELOCATION_VERIFIED`。** 在无活动竞赛故障时，将 Worker1 上 SF-007 ATTEMPT-01 的完整历史归档从 `/data/rca-archives/ops-competition-2026/SF-007-ATTEMPT-01/` 移至 `/root/ops-competition-2026/relocated-archives/SF-007-ATTEMPT-01/`。原路径现为指向新位置的符号链接；Master 原始采集保持不变。

原始归档 3,626 个文件、4,370,307,450 B，Manifest SHA-256 `45db324015272c190e7aef917594573a1b8a194b0d84aa4c949662071eafe8d5`。迁移前、新路径、原路径链接均由 `competition_archive.py verify` 完成逐文件 SHA 和 Metrics/Logs/Traces 恢复读回，三份回执位于 Worker1 `/root/ops-competition-2026/relocation-audit/SF-007-ATTEMPT-01-{before,after,via-original}.json`。

迁移后 Worker1 系统盘可用 55,992,659,968 B，数据盘可用 16,744,697,856 B，均高于十进制 5 GB 下限。SF-007 ATTEMPT-05 的五主机容量预检及残留审计随后通过。没有删除或改写历史采集、Public、Private GT 或 Manifest，也没有访问比赛服务器。
