# SF-011 ATTEMPT-04 独立对照校准依据

**状态：DRAFT，尚未执行正式故障。** SF-011 ATTEMPT-03 的 HTTP 首页延迟机制、完整三模态和目标直接证据均通过，但 Image 正常对照 after/before p95 为 1.751/1.189 ms，约 1.47 倍，超过该 Attempt 冻结的 1.25。它永久保持 `FAILED_ADMISSION`，不重评、不创建 GT。

新 ATTEMPT-04 只对 Image 的亚毫秒级正常延迟噪声使用 `max(1.25 × before_p95, before_p95 + 2.0 ms)` 上限。这个 2 ms 绝对容差不是根据 ATTEMPT-03 制定：先前独立无故障 VAL5/VAL6 校准文件 SHA-256 `7b347645e8ce1525b7f1bfdbb6f85bbc4a3e6e2226a94656e38e891e41de19b0` 已记录 VAL6 Image p95 1.628→2.164→3.123 ms，且 `formal_results_used=false`；更早的 SF-011 v2 Admission 草案也已有 `image_absolute_p95_tolerance_ms: 2.0`，该草案 SHA-256 `3ca4a14e28cc10b25ef7a7bff031ca829141528fa2a77ee430c7c9235678b81e`。新合同 SHA-256 `a3f0762cd1b7b374b4ef35ed27f8f75d742f3a137f29a77956fd60a63b162c34`。

Cart 对照仍严格使用 1.25 倍，无 2 ms 例外；Image/Cart 的前、中、后有效请求数、Proxy Span 关联、首页故障强度与恢复、Collector 拒收 0、六段采集及 5 GB 容量底线均保持。ATTEMPT-04 必须先完成新注册的完整 1,800 秒无故障五主机资格，再冻结输入源码与合同、刷新身份和健康检查后才能执行。即使它成功，也应披露单 HTTP 首页延迟与 SF-004 的机制重复，不能把新题号当作异常多样性。
