# 组委会逐题审核表

正式题每题 1 张图、3 问、10 分；本文件及答案留在 Private。

## Q01 · Frontend Pod 内存使用曲线

Case SF-003；目标 frontend-68d8bbfdc8-8mnzh；窗口 2026-10-16T03:26:49.724275+00:00 至 2026-10-16T03:56:49.724275+00:00 UTC。

1. 整段时间内，最高的每分钟均值是多少（单位 MiB）？ **163.33 MiB**；计算：max of valid minute values。
2. 最高值出现在哪个 UTC 分钟起点？若并列，取最早一分钟。 **2026-10-16T03:36:49.724275+00:00 UTC**；计算：earliest maximum minute start。
3. 整段时间内所有有效分钟值的算术平均是多少（单位 MiB）？ **130.81 MiB**；计算：unweighted mean of valid minute values。

![参考图](reference-charts/Q01.png)

审核意见：____________________

## Q02 · Frontend Pod CPU 使用曲线

Case SF-003；目标 frontend-68d8bbfdc8-8mnzh；窗口 2026-10-16T03:26:49.724275+00:00 至 2026-10-16T03:56:49.724275+00:00 UTC。

1. 整段时间内，最高的每分钟均值是多少（单位 cores）？ **0.0628 cores**；计算：max of valid minute values。
2. 最高值出现在哪个 UTC 分钟起点？若并列，取最早一分钟。 **2026-10-16T03:51:49.724275+00:00 UTC**；计算：earliest maximum minute start。
3. 整段时间内所有有效分钟值的算术平均是多少（单位 cores）？ **0.0461 cores**；计算：unweighted mean of valid minute values。

![参考图](reference-charts/Q02.png)

审核意见：____________________

## Q03 · Frontend 两个副本的内存对比

Case SF-003；目标 frontend-68d8bbfdc8-8mnzh, frontend-68d8bbfdc8-c4n85；窗口 2026-10-16T03:26:49.724275+00:00 至 2026-10-16T03:56:49.724275+00:00 UTC。

1. 哪一个实体的整段有效分钟值均值更高：frontend-68d8bbfdc8-8mnzh 还是 frontend-68d8bbfdc8-c4n85？ **frontend-68d8bbfdc8-c4n85 entity ID**；计算：compare unweighted means of valid minutes。
2. 两条曲线的整段分钟均值相差多少（绝对值，单位 MiB）？ **1.34 MiB**；计算：absolute difference of per-series minute means。
3. 两条曲线的差距（绝对值）最大时，UTC 分钟起点是什么？若并列，取最早一分钟。 **2026-10-16T03:36:49.724275+00:00 UTC**；计算：earliest maximum absolute per-minute difference。

![参考图](reference-charts/Q03.png)

审核意见：____________________

## Q04 · Recommendation 每分钟调用 Span 数量

Case SF-005；目标 recommendation；窗口 2026-10-16T11:23:55.458636+00:00 至 2026-10-16T11:53:55.458636+00:00 UTC。

1. 整段时间内共有多少条符合条件的 服务端 Span？ **3331 spans**；计算：sum of all 60-second bucket counts。
2. 数量最多的一分钟有多少条 服务端 Span？ **131 spans**；计算：maximum minute count。
3. 数量最多的分钟起点 UTC 是什么？若并列，取最早一分钟。 **2026-10-16T11:49:55.458636+00:00 UTC**；计算：earliest maximum minute start。

![参考图](reference-charts/Q04.png)

审核意见：____________________

## Q05 · Recommendation 错误日志数量

Case SF-005；目标 recommendation；窗口 2026-10-16T11:23:55.458636+00:00 至 2026-10-16T11:53:55.458636+00:00 UTC。

1. 整段时间内共有多少条符合条件的 ERROR 日志？ **9 logs**；计算：sum of all 60-second bucket counts。
2. 数量最多的一分钟有多少条 ERROR 日志？ **9 logs**；计算：maximum minute count。
3. 数量最多的分钟起点 UTC 是什么？若并列，取最早一分钟。 **2026-10-16T11:33:55.458636+00:00 UTC**；计算：earliest maximum minute start。

![参考图](reference-charts/Q05.png)

审核意见：____________________

## Q06 · Frontend 到商品目录的错误调用数量

Case SF-005；目标 frontend -> product-catalog；窗口 2026-10-16T11:23:55.458636+00:00 至 2026-10-16T11:53:55.458636+00:00 UTC。

1. 整段时间内共有多少条错误调用边 Span（单位 error spans）？ **58 error spans**；计算：sum of all minute error-span counts。
2. 错误 Span 数量最多的分钟起点 UTC 是什么？若并列，取最早一分钟。 **2026-10-16T11:33:55.458636+00:00 UTC**；计算：earliest maximum minute start。
3. 从 11:33:55 UTC 起的 5 分钟总数减去从 11:23:55 UTC 起的 5 分钟总数，差是多少（单位 error spans）？ **58 error spans**；计算：second five-minute count minus first five-minute count。

![参考图](reference-charts/Q06.png)

审核意见：____________________

## Q07 · Frontend Proxy 入口调用 p95 时延

Case SF-010；目标 frontend-proxy；窗口 2026-10-15T16:37:43.727538+00:00 至 2026-10-15T17:07:43.727538+00:00 UTC。

1. 整段时间内，最高的每分钟 p95 是多少（单位 ms）？ **256.99 ms**；计算：max of minute nearest-rank p95 values。
2. 最高值出现在哪个 UTC 分钟起点？若并列，取最早一分钟。 **2026-10-15T16:53:43.727538+00:00 UTC**；计算：earliest maximum minute start。
3. 从 16:52:43 UTC 起的 5 个分钟 p95 的均值，减去从 16:37:43 UTC 起的 5 个分钟 p95 的均值，差是多少（单位 ms）？ **48.03 ms**；计算：unweighted mean of five p95 values: second window minus first。

![参考图](reference-charts/Q07.png)

审核意见：____________________

## Q08 · Frontend 每分钟调用 Span 数量

Case SF-010；目标 frontend；窗口 2026-10-15T16:37:43.727538+00:00 至 2026-10-15T17:07:43.727538+00:00 UTC。

1. 整段时间内共有多少条符合条件的 服务端 Span？ **52753 spans**；计算：sum of all 60-second bucket counts。
2. 数量最多的一分钟有多少条 服务端 Span？ **1915 spans**；计算：maximum minute count。
3. 数量最多的分钟起点 UTC 是什么？若并列，取最早一分钟。 **2026-10-15T16:38:43.727538+00:00 UTC**；计算：earliest maximum minute start。

![参考图](reference-charts/Q08.png)

审核意见：____________________

## Q09 · Frontend 到商品目录的调用边 p95 时延

Case SF-010；目标 frontend -> product-catalog；窗口 2026-10-15T16:37:43.727538+00:00 至 2026-10-15T17:07:43.727538+00:00 UTC。

1. 整段时间内，最高的每分钟 p95 是多少（单位 ms）？ **84.51 ms**；计算：max of minute nearest-rank p95 values。
2. 最高值出现在哪个 UTC 分钟起点？若并列，取最早一分钟。 **2026-10-15T16:47:43.727538+00:00 UTC**；计算：earliest maximum minute start。
3. 从 16:47:43 UTC 起的 5 个分钟 p95 的均值，减去从 16:37:43 UTC 起的 5 个分钟 p95 的均值，差是多少（单位 ms）？ **14.77 ms**；计算：unweighted mean of five p95 values: second window minus first。

![参考图](reference-charts/Q09.png)

审核意见：____________________

## Q10 · Frontend 与 Cart 调用时延对比

Case SF-010；目标 frontend, cart；窗口 2026-10-15T16:37:43.727538+00:00 至 2026-10-15T17:07:43.727538+00:00 UTC。

1. 哪一个实体的整段有效分钟值均值更高：frontend 还是 cart？ **frontend entity ID**；计算：compare unweighted means of valid minutes。
2. 两条曲线的整段分钟均值相差多少（绝对值，单位 ms）？ **25.93 ms**；计算：absolute difference of per-series minute means。
3. 两条曲线的差距（绝对值）最大时，UTC 分钟起点是什么？若并列，取最早一分钟。 **2026-10-15T16:47:43.727538+00:00 UTC**；计算：earliest maximum absolute per-minute difference。

![参考图](reference-charts/Q10.png)

审核意见：____________________
