# Worker3 历史归档盘位迁移

**状态：ARCHIVE_RELOCATION_VERIFIED。** 在没有活动竞赛 Timer、故障或后台实验时，将 Worker3 的 `SF-010-ATTEMPT-07` 历史归档从 `/data/rca-archives/ops-competition-2026/` 移至同机系统盘 `/root/ops-competition-2026/relocated-archives/`，并在原路径建立受保护链接。Master 原始数据和 Public/Private 包未改动；没有访问 50/51 比赛服务器。

迁移前，Worker3 `/data` 可用 5,255,241,728 B，系统盘可用 62,044,590,080 B；按当前未完成题预算预检为 `STOP_NEW_WORK`。目标归档 raw 有 2,659 文件、5,834,704,872 B，Manifest SHA-256 为 `eeeba6f1dcad8026c3aeadaed1b8feeef997b2231a762ca8a2bac2f285232079`；outputs 有 11 文件、41,313,713 B，Manifest SHA-256 为 `7b43689fd3d65893b6de460aac091e39a746068b778be438a4379e5ac892a69c`。两个 Manifest 与正式生产总账一致。

raw 与 outputs 均在原位置、迁移后位置、原路径链接三个阶段完成逐文件 SHA 和恢复读回，状态均为 `COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS`。原始审计文件位于 Worker3 `/root/ops-competition-2026/relocation-audit/SF-010-ATTEMPT-07-*`。迁移后 `/data` 可用 11,104,210,944 B，系统盘可用 56,161,861,632 B，均高于十进制 5 GB。旧访问路径仍指向同一完整归档。

后续五主机故障残留、竞赛单元、Kubernetes Ready、Collector 健康和按 Worker2 归档的 5 GB 容量预检均通过。迁移只改变受保护归档的物理盘位，不改动 Manifest、原始采集、GT 或比赛服务。
