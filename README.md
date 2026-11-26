# 准入 Webhook 降级契约与豁免庭

离线模拟 Kubernetes 准入 Webhook 的契约、租户豁免和请求裁定。只保存合成对象摘要和回执，不连接真实集群。Python 3.11+ 标准库即可运行。

三个独立入口：`contract` 决定契约代际、范围与超时模式；`waiver` 决定租户、UID、路径和期限内的例外；`admit` 固定请求契约快照，收集调用并形成可复算准入结论。强制契约不能被豁免；契约换代或撤销使未决请求与旧豁免失效。

```sh
python -m admissioncourt demo --db demo.sqlite
python -m admissioncourt replay --db demo.sqlite
python -m unittest discover -s tests -v
```

三个入口也可依次执行 `fixtures/contract_normal.json`、`waiver_normal.json`、`admit_normal.json` 和对应 `_failure.json`。重复事件按 `event_id` 和原文幂等，冲突内容明确拒绝。`replay` 在空库重新执行每一条事件，并逐业务表比较持久状态。示例使用固定逻辑时刻，不存真实凭据。生产系统需另行实现 Webhook 网络层与可靠的操作者身份认证。

`python -m admissioncourt dispatch --db demo.sqlite --fixture fixtures/dispatch_normal.json` 基于 demo 数据库按接收容量预留合成清单。输入必须引用已经准入的请求及其最终摘要。

`python -m admissioncourt projections --db demo.sqlite --fixture fixtures/projections_normal.json` 查看实际已提交请求和给定基线的字段变化。

`python -m admissioncourt checkpoint --db demo.sqlite --fixture fixtures/checkpoint_normal.json` 从数据库历史的指定已提交事件切点重建离线状态。

`python -m admissioncourt receiver --db demo.sqlite --fixture fixtures/receiver_normal.json` 在合成接收器中应用获得槽位的清单并持久记录回执。

`python -m admissioncourt endorse --db demo.sqlite --fixture fixtures/endorse_normal.json` 导出同一契约修订上尚未过期的双角色签署证明。

`python -m admissioncourt settlement --db demo.sqlite --fixture fixtures/settlement_normal.json` issues a synthetic service invoice from recorded receiver receipts. Run the receiver fixture first. The original invoice and line prices are retained for repeat calls.

`python -m admissioncourt console-output --fixture fixtures/console_output_normal.json` exposes the captured text in a synthetic multi-process fixture. It does not start native processes.
