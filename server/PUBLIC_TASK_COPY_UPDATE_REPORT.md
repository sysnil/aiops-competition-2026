# 练习、半决赛与决赛公开题面文案修订

状态：`PUBLIC_COPY_UPDATED_AND_VERIFIED`（2026-10-20）。

51 的公开 Question Catalog 已更新 3 道练习题和 Q01–Q10 的 `title`、`description`。标题现在标明分析对象、观测数据和统计/对比任务，例如“Checkout Pod 内存：峰值与窗口对比”“Recommendation：ERROR 日志分钟统计”“Frontend → Product Catalog：p95 时延”。通用的重复说明已改成题目已有查询口径的简明摘要。每个查询题的 Case、时间窗、查询过滤、聚合规则、图表要求、三问原文和发布状态保持不变。

Final Catalog 的 3 个 FT 任务与 8 个 PK 轮次增加了公开 `title`、`description`，51 Final Task API 在授权后返回这两个字段。标题按“上半场第 N 题”与“下半场第 N 轮”区分赛段；说明只描述结构化异常调查与证据提交，不暗示根因、注入方式或标准答案。原 `public_prompt`、Case 绑定、提交额度和赛制状态未变。PK-R05–R08 仍没有绑定 Case，未新增第 12 个正式任务。

选手 Web 52–68 与组委会预览 Web 50 均在题目选择区显示当前查询题标题和说明，决赛任务列表/详情显示 Final 标题和说明。快速切换 Practice/Semifinal 时，前端会取消过期的 Case/Question 请求，防止旧响应覆盖新列表；低于 1180px 的布局改为纵向排列，桌面投屏布局保持原样。

## 验证

- 旧/新 Catalog 结构化对比通过：13/13 Question 与 11/11 Final Task 仅标题、说明变化；公开 Prompt、GT、评分代码和数据不变。
- 51 隔离回归测试 `28 passed, 3 skipped`；API 重启后 `/healthz` 正常。TEAM-01 真实权限下可读取 3 道练习题和 10 道 AM 预览题的新标题与说明。50 的授权内部预览可读取 7 个已绑定 Final Task 的新标题与说明。
- 52–68 共 17 台静态 Web 文件哈希与健康检查通过；50 组委会 Web 同样通过。Playwright 验证真实 Semifinal 题面与本地模拟的 Final 授权页面在桌面和 390px 手机视口中无脚本错误或横向溢出。模拟 Final 截图只用于排版测试，不表示队伍已获权或题目已开放。
- 52 的 `/api/final/tasks` 仍为 HTTP 404；11 个 Final Task 仍 `NOT_OPEN`，正式 Final 提交数 0。Practice `OPEN`，AM 预览内测 `OPEN`，PM `NOT_OPEN`。已批准 Final GT Manifest SHA-256 仍为 `918c90331f260075987ca16164088c76fb2d716d73fe0ee5e631c2dba65d331c`。

## 版本与回滚

- 51 当前 `questions.json` SHA-256：`030446a963ef9eb23edd106006f062c72a0fc6b83bfa641044ea8b6293f64dbb`；Final Catalog：`34488b76e8463f5edda8642e5ed146991894ab8e067c115be3eda322164147ab`；Final API：`20caa678bb8a8b2a89c8c1c9a2330a163837514784ea141c8a1d2903e45aa0ee`。旧版备份于 51 `/opt/aiops-contest-server/backups/public-copy-20261020/`。
- 52–68 最终静态 JS SHA-256：`20e459a5aaa3e26157004b2a3fae882b0764081196943aa400962bf301e70ce9`；CSS：`bac64d2ba81c716fb76c4931e2a958d4e0fb4a010736abf52d82d1e7cc71775d`。每台的前两个版本分别保存在 `webapp/backups/public-copy-20261020/` 和 `webapp/backups/public-copy-20261020-responsive/`。50 的旧 Web 静态文件保存在同项目 `webapp/backups/public-copy-20261020/`。

截图：`competition-player-web/reports/public-copy-semifinal-52.png`、`public-copy-mobile-52.png`、`public-copy-organizer-desktop.png`、`public-copy-organizer-mobile.png`。`public-copy-final-mocked-52.png` 与 `public-copy-final-mobile-mocked-52.png` 明确为模拟授权画面。
