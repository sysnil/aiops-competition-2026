
# 系统架构

```text
Master 靶场
  业务负载 → 有界故障adapter → Metrics Logs Traces Kubernetes → Public包
                                                          └→ Private GT/注入证据
比赛服务器51
  Public Parquet/Manifest → FastAPI查询API → 队伍Token与IP/场次鉴权
  选手提交 → 不可变答案文件 → SQLite/队列 → Private Grader → 分数与Breakdown
  组委会标注Web / 提交证据Web / 半决赛和决赛上半场排名
选手服务器及50测试机
  官方CLI或Web → Public API调查 → answer.json/图表/证据 → 51提交接口
```

## 模块边界

- `server/app/storage.py`读取固定历史窗口的Public Parquet，依赖Manifest定位实际文件。
- `server/app/access_control.py`和`security.py`处理身份及权限。实际registry与Token不入Git。
- `server/app/questions.py`及`config/questions.json`提供QA题面。
- `server/app/final_api.py`和`final_store.py`处理决赛任务、轮次、提交及持久化。
- `server/grading/submission_api/`校验证据并排队，`private_grader/`执行私有评分。
- `organizer/visualization/`查看多模态证据并辅助审核；`organizer/submissions/`展示提交、答案文件和排名。
- `organizer/team-management/`是分发工具源码，没有真实队伍凭据。

数据目录与文件格式以`datasets/public-catalog/`的Manifest为准；仓库中没有大体量原始遥测。Private Grader与Public API的数据可见性需要在部署时继续隔离。
