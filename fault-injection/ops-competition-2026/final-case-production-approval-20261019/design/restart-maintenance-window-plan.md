# DRAFT: 决赛授权主机重启生命周期测试

状态：**未获重启许可、未执行**。当前 Master/Worker 主机没有现成的可独立启动 systemd 的测试 VM/rootfs；仅有 `runc`，不能用进程命名空间模拟宿主机 PID 1、transient Timer、SSH 启动和 cgroup/网络恢复。boot ID 改变的无害测试仅是协议模拟，不是主机重启 PASS。

## 优先隔离环境

由组委会提供 Master、Worker2、Worker3 的可丢弃 VM 克隆，至少保持与生产一致的 Linux、systemd、OpenSSH、Timer 精度与授权代码版本。测试网络不得路由到生产 Kubernetes/业务网段；仅让三台克隆相互通信。克隆应使用独立主机密钥与测试签名密钥，不能复制生产私钥。记录 VM 镜像/配置 SHA、boot ID、系统版本和网络隔离证明。若克隆无法证明这些语义一致，结果仅可作为工程参考，不能替代维护窗口验证。

## 如需生产维护窗口，单独报批

1. 组委会逐台批准具体主机、UTC 窗口和带外控制台：先 Master，再 Worker2，再 Worker3；各主机一轮结束并恢复健康后才能进入下一轮。不得在现有真实业务故障执行中进行。Master 重启会暂时中断 Kubernetes 控制面/采集查询；Worker2/3 重启可能影响 Frontend、Recommendation、Collector 等 Pod，需提前确认业务影响、恢复负责人和停止条件。
2. 预检五主机磁盘十进制 5 GB 底线、4 Nodes Ready、Calico 3/3 BGP、Collector/Prometheus/OpenSearch/Jaeger、Pod UID/placement、SSH 管理与带外通道、竞赛 Timer/Service 零残留。任一失败则取消测试。
3. 只注册新的、带 Manifest SHA 的无害 Marker Attempt。记录 Controller/Guard/Worker Timer 单元、精确计划时间、请求/签名公钥 SHA、Master 与目标 Worker boot ID、撤销动作与独立失败终态。不得加载真实故障适配器。
4. Timer pending 时重启一台获批主机。恢复后等待超过原触发时间，验证 Marker 不存在、Timer/Service 不再 pending、Guard 有可审计终态、授权被拒或永久撤销。分别重复：未撤销 Attempt、重启前已撤销 Attempt、授权签发前后发生重启。不能因为 `Persistent=false` 直接判 PASS。
5. Master 重启后须验证 boot ID 变化、原 Controller/Guard 不会被旧状态误认，Worker2/3 无法获得旧 Attempt 授权。Worker2/3 重启后须验证本地 Timer 是否恢复、工作负载身份是否改变、旧 Worker boot ID 绑定拒绝、签名响应不得重放。
6. 一旦 Marker 在已宣布 ABORT/REVOKED 后触发，立即停止该批测试并保留全部原始证据。恢复经授权的两条 SSH 强制命令、各自源 IP 限制和签名公钥；执行五机健康、磁盘、qdisc、进程/Timer 与历史 Case SHA 终检。

独立恢复人可经带外控制台仅停止本次 Marker 单元并写入该 Attempt 撤销标记。不得修改历史 Case/GT，不能为腾空间删除失败记录。最终由组委会审查原始 journald、Timer 属性、重启前后 boot ID 和五机恢复证据，决定重启门禁是否 PASS；本方案本身不是生产许可。
