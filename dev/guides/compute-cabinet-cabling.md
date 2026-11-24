# 计算柜双归布线

一个计算柜按一个接入设备建模，柜内 8 台 GPU 服务器的 32 个 OSFP 口统一编号。本功能生成计算柜到既有 RoCE Leaf 的光纤规划和连接记录。Bond 由主机配置，本功能负责物理端口对应和 A/B 面。

## 规则与验收

- 每四个计算柜共享一对 Leaf。奇数柜口接这对 Leaf 的第一台，偶数柜口接第二台。
- 每个计算柜在每台 Leaf 上占连续 16 个物理口。物理口一对一，A 接 A、B 接 B。
- 512 个柜与 256 台 Leaf 应产出 16,384 组物理口配对和 32,768 条 400G 面级链路。
- 校验下联口范围、柜口奇偶分配、可用 Leaf 数量和现有端口占用。生成时复用已有 Leaf，写入缺失对象。

## 界面操作

1. 切到已生成 Leaf 的 RoCE 拓扑所在分支。
2. 在 **Network → Compute Cabinet Cabling Designs** 创建设计，选择 **RoCE Fabric** 和 **Cabinet Device Type**，并加入 `compute_cabling_designs` 组。
3. 全规模填写 **Cabinet Count** 512、**Cabinet Port Count** 32、**Cabinets Per Leaf Pair** 4、**Leaf Port Start** 1、**Leaf Port End** 64。按两端光模块选 **Medium**。
4. 在 **Actions → Generator Definitions** 运行 `validate-compute-cabling`。日志应报告 16,384 组物理口和 32,768 条链路。再运行 `generate-compute-cabling`。
5. 到 **Network → Connections** 按设计名搜索。接口名称如 `1/A`，斜线前是物理口，斜线后是光模块面。
6. 同步新版仓库后，在测试分支创建 Proposed Change，从 **Artifacts** 打开 **Combined RoCE Cabling Plan**。CSV 将 Leaf 到 Spine 和 Leaf 到计算柜合在一个表中，按 Leaf 编号、物理端口、A/B 面排序。每根 400G 光纤一行，标签格式为 `设备名:物理端口/面 to 设备名:物理端口/面`。Excel 可直接打开。该产物根据设计参数生成，可在创建 Connections 前预览。仅需计算柜下联时，可打开 **Compute Cabinet Cabling Plan**。

小规模试跑可在至少有两台 Leaf 的 RoCE 拓扑上使用 2 个柜，其他参数保持默认。预期 128 条面级链路，前两个柜使用 Leaf 1、2 的 1–32 口。
