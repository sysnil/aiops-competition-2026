# Worker2/Worker3 历史归档盘位迁移

**状态：`ARCHIVE_RELOCATION_VERIFIED`。** 在无活动竞赛故障、五主机残留 PASS 后，将已在 Master 保留原件的历史归档从对应 Worker 的 `/data/rca-archives/ops-competition-2026/` 迁至同机 `/root/ops-competition-2026/relocated-archives/`。原路径保留符号链接。每份 raw 和存在的 outputs 均在迁移前、新位置和原路径链接下完成原 Manifest 逐文件 SHA 与可用的三模态恢复读回。

| 主机 | Attempt | raw Manifest SHA-256 | 其他输出 Manifest SHA-256 |
|---|---|---|---|
| Worker3 | SF-004-ATTEMPT-03 | `520e9d0ade2a048824fc1ee250a7737c72356694c0eaa5986478fa8650176228` | 无 |
| Worker3 | SF-004-ATTEMPT-05 | `93e826ecfa10f47594f1182550c548d009ae6cea2d77ab41af387b235f3d0841` | `dc29dceee55b0aa09ac3cc11ba87794d986cf42ec47a09d8703f611f843abc66` |
| Worker3 | SF-005-ATTEMPT-02 | `ca6daef56db86dbd246df7cbd7e81baab076aeda9c6b92b946d9a521a405a499` | 无 |
| Worker3 | SF-005-ATTEMPT-04 | `d201d77a7da0403ad4cec3e3ac76fe70d4646ccf0e1e5c8728586894a961e739` | `a7e5057633fcfa518a250b4cced374b6562484a43590bb69a859881e8eaa7b82` |
| Worker2 | SF-011-ATTEMPT-01 | `92804be03368631b117e4efccf937e4b9474b451e1ae9993e2aaa268c49f55f3` | rejected outputs `0d019f276f40d9f421baac21ff06ecc8586c5ffd5aa4cd33f4cb57f2538c5a33` |
| Worker2 | SF-011-ATTEMPT-02 | `4736d7e5b75df0752d3d55df45272b248ad63cf6dff49f0459dfa0afbb8a04f4` | rejected outputs `0438af1ebc5ecb9c130a0c19ee8d0c7bdeac8079b96ff9ddb799a2d3074103c8` |

原始 Manifest 哈希与 Master `/data/stage5-v3/ops-competition-archive-index/<Attempt>/` 对应文件一致；逐项回执位于各 Worker `/root/ops-competition-2026/relocation-audit/`。迁移后 Worker3 系统盘/数据盘可用约 41.08/22.84 GB，Worker2 约 54.00/19.65 GB，均高于十进制 5 GB。随后五主机残留、竞赛单元及原执行器的单题动态容量预检均 PASS。

该操作只变更 Worker 上冗余归档的物理盘位，不删除历史 Attempt、不修改失败 Admission 或 READY 题、不触碰 Private GT 或比赛服务器。SF-011 ATTEMPT-03 的正式采集和故障准入仍由新鲜独立门禁决定。

后续为 SF-011 ATTEMPT-04 增加 Worker3 空间时，SF-005 ATTEMPT-04 的 raw 2,560 文件/5,647,529,498 B 和 outputs 15 文件/39,395,463 B 又分别在迁移前、新位置及原路径链接三次通过原 Manifest SHA/读回。此后 Worker3 系统盘/数据盘可用约 35.39/21.91 GB，五主机无故障残留和单题容量预检 PASS。此前 SF-011 ATTEMPT-03 的失败准入结论不变。
