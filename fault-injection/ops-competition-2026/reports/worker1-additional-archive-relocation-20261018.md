# Worker1 两份历史归档迁移

**状态：`ARCHIVE_RELOCATION_VERIFIED`。** 在 SF-007 ATTEMPT-05 安全中止、五主机残留与竞赛单元均 PASS 后，将两份已校验的历史归档从 Worker1 `/data/rca-archives/ops-competition-2026/` 移至 `/root/ops-competition-2026/relocated-archives/`。原路径均保留符号链接，Master 原始 Case 保留，未访问比赛服务器。

| Attempt | 内容 | Manifest SHA-256 | 文件/字节 |
|---|---|---|---:|
| SF-012-ATTEMPT-02 | raw | `8b1157cd548ef443213e80ad43d57aba51af40d0bdeb79efd5c0af0ebc8a5050` | 3,809 / 4,595,323,337 |
| SF-008-ATTEMPT-05 | raw | `234428ca9163024d5387f2d0efeca43a3403954b004e21c2a8b4330303f191c5` | 3,734 / 4,486,522,414 |
| SF-008-ATTEMPT-05 | outputs | `b432a2455782d9a4390ba25864123a806d2a793fb73c9c61b2e2a962d7aa596e` | 10 / 141,883,289 |

每份内容在迁移前、新位置和原路径链接下均经 `competition_archive.py verify` 逐文件 SHA 与可用的 Metrics/Logs/Traces 恢复读回。回执位于 Worker1 `/root/ops-competition-2026/relocation-audit/`，名称为相应 Attempt 的 `*-before.json`、`*-after.json`、`*-via-original.json`。迁移后 Worker1 系统盘可用 45,951,197,184 B，数据盘可用 24,969,031,680 B，均高于十进制 5 GB 下限。

迁移只改变 Worker1 冗余归档的物理盘位；没有删除历史证据、修改 Manifest、Public、Private GT 或降低存储预测保护。SF-007 的下一次真实执行仍须通过新的完整无故障容量资格和新鲜安全门禁。

## 12:24 后续盘位调整

SF-011 ATTEMPT-04 因 Worker1 后续空间预测触发原 5 GB 安全门禁并中止后，在无活动故障时又迁移了两份已 READY 且 Master 保留原件的 Worker1 归档。每份在迁移前、新位置与原路径链接下，raw 和 outputs 分别三次通过原 Manifest SHA 与可用的三模态恢复读回，回执仍位于 Worker1 `/root/ops-competition-2026/relocation-audit/`。

| Attempt | 内容 | Manifest SHA-256 | 文件/字节 |
|---|---|---|---:|
| SF-007-ATTEMPT-08 | raw | `5e661004894420e28acc4c627e31cf582d55393b5a40fd1a92bdb53156f5d01f` | 3,681 / 4,449,728,896 |
| SF-007-ATTEMPT-08 | outputs | `feb669aaba1d8e57730bbd7a35f0eaa87ec2203f29e439db7710bc0c44447c15` | 11 / 31,017,028 |
| SF-012-ATTEMPT-04 | raw | `58cd038d452cd0b8d1f45dea329bd085acfbe9f5eac913f01963799c7998611c` | 3,803 / 4,592,247,337 |
| SF-012-ATTEMPT-04 | outputs | `9a081dd7ab56b9813581e873a6e2e0b40f8cc784d580bc8fc1eba2f0f332fe12` | 11 / 31,845,178 |

调整后 Worker1 系统盘可用 36,799,447,040 B，数据盘可用 22,797,295,616 B；原路径链接有效，Master 原始 Case 和完整 Public/Private 包不变。随后五主机残留、竞赛单元和原单题容量预检 PASS。此操作不是删除 READY 题目，也没有将数据复制到 51。
