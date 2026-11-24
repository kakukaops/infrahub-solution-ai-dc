# RoCE Leaf–Spine 布线技术方案

新增 `NetworkRoceCablingDesign` 设计节点，保存两端设备型号、数量、物理口起止编号、规则和线缆介质。使用独立生成器创建 `NetworkDevice`、`NetworkInterface` 和 `NetworkLink`，通过生成器目标组运行。设备所属设计用于识别已有输出。

## 端口映射

从 1 开始编号设备。对 Leaf 编号 L、物理口 P 和叶端上联起始口 S：

- Leaf 的 A 面对应 Spine `2 × (P − S) + 1`，B 面对应下一台 Spine。
- Spine 的物理口是其起始口加上 `(L − 1) // 2`。
- Spine 面在 Leaf 编号为奇数时为 A，偶数时为 B。

纯 Python 计划函数在写入前验证容量。API 层预检设备归属、已有端口和链路，之后按设备、接口、链路顺序分批写入。名称和接口身份可复算，重试时复用已匹配的对象。需要重新接线时使用独立设计，并在分支中审核结果。

## 操作

1. 导入 schema、菜单和 `roce_cabling_designs` 组，同步包含本功能的 Git 版本。
2. 在新分支打开 **Network → RoCE Cabling Designs**，创建对象，并加入 `roce_cabling_designs` 组。
3. 在 **Actions → Generator Definitions** 运行 `validate-roce-cabling`，选择设计，查看任务日志中的计划数量和校验结果。
4. 运行 `generate-roce-cabling`，选择同一设计。完成后在 **Network → Connections** 搜索设计名。链路详情的 **Endpoints** 显示两端设备和接口名。

首次部署要重建包含更新后 Python 包的方案镜像。大规模运行的耗时取决于 Infrahub 的 API 和任务执行器容量。

## 参数示例

| 字段 | 三台 Leaf 试跑 | 全规模 |
| --- | ---: | ---: |
| Leaf count | 3 | 256 |
| Spine count | 2 | 128 |
| Leaf port start / end | 65 / 65 | 65 / 128 |
| Spine port start / end | 1 / 2 | 1 / 128 |
| Rule | Paired A/B | Paired A/B |

两种规模都应选择与实际硬件对应的设备型号。全规模要求每台 Spine 有 256 个可用的 400G 端点。端口范围与型号的兼容性由录入参数的人按规格确认。

开发时可以让命令行直接读取当前工作区的生成器，前提是目标分支已经加载新 schema 并创建了设计对象。以下示例使用名为 `roce-smoke` 的设计：

```bash
uv run infrahubctl generator validate-roce-cabling --branch demo/roce-cabling design_name=roce-smoke
uv run infrahubctl generator generate-roce-cabling --branch demo/roce-cabling design_name=roce-smoke
```

如果写入期间中断，等待该任务结束，然后用原参数重新运行。更改已生成的连线前，先在独立设计中检查新方案。

## 全规模任务的追踪与恢复

生成任务分别记录预检、设备写入、接口写入和链路写入的耗时及新建数量。重试从设备关系分批读取接口及链路，在内存中核对端点，只写入缺失的对象。新对象的写入并发限制为 10。

SDK 追踪组每次只登记设备。接口由设备关系归属，链路通过两端接口核对。新版使用独立的设备追踪组，并关闭该运行的自动清理，避免历史运行的成员在重试时被删除。若设计缩小或已有链路与新规则冲突，生成器拒绝变更。现有设备和链路的清理需要单独审核。
