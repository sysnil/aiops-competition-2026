# Worker1 历史归档盘位迁移记录

**状态：ARCHIVE_RELOCATION_VERIFIED。** 2026-10-17 在无正式故障运行时，将两份已归档的比赛 Attempt 从 Worker1 `/data` 迁到同机系统盘 `/root/ops-competition-2026/relocated-archives/`。原路径保留指向新位置的链接；Master 原始 Case 保留，未访问 192.168.51.51，未发布题目或 GT。

| Attempt | 原路径与新路径的 raw Manifest SHA-256 | 文件/字节 | 其他输出 |
|---|---|---:|---|
| SF-008-ATTEMPT-04 | `35773cc6c9e98f0867901c70894d6f7179bf79a269112869a0a52440851a711c` | 2,250 / 4,840,279,825 | 无 |
| SF-006-ATTEMPT-01 | `31c9488aedd810de25de1c1649dda2a94a6d520d281da992249b9af7252ef93c` | 3,751 / 4,542,460,018 | outputs: 15 文件、144,278,720 B；Manifest SHA `d80202a9d9cd62537eca81689edc878a8c7f173fd880afd22aca5aa7e2150b98` |

两份 raw 在迁移前、新位置及原路径链接均通过 `competition_archive.py verify` 的逐文件 SHA 和 Metrics/Logs/Traces 恢复读回。SF-006 outputs 也在新位置和原路径通过 SHA 读回。Worker1 审计文件位于 `/root/ops-competition-2026/relocation-audit/`，对应 `*-before.json`、`*-after.json`、`*-via-original.json`。

原路径 `/data/rca-archives/ops-competition-2026/<Attempt>` 现指向 `/root/ops-competition-2026/relocated-archives/<Attempt>`。新位置受 `/root` 的 root-only 路径权限保护。迁移前 Worker1 系统盘/数据盘可用分别为 69,692,760,064 / 12,169,998,336 B；迁移后分别为 60,142,129,152 / 21,619,908,608 B，均高于 5,000,000,000 B。

迁移后五主机残留、竞赛单元、live health 和按 SF-008 Worker2 归档位置的容量快照均 PASS。该迁移只处理冗余归档的物理盘位，不修改历史原始采集、Public、Private GT 或 Manifest。
