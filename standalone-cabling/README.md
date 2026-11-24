# RoCE 单机布线规划

本地程序通过浏览器显示图形界面。安装依赖后可离线运行。 两套规则、输入参数和验收标准见[需求文档](REQUIREMENTS.md)。

## 用 Docker Compose 部署

交付时复制整个 `standalone-cabling/` 目录。客户主机只需安装 Docker 和 Docker Compose v2。
首次在线构建需要访问 Docker Hub 和 PyPI。程序运行时可离线，不依赖 Infrahub 或数据库。

```bash
docker compose up -d --build --wait
docker compose ps
docker compose logs -f cabling
docker compose down
```

默认访问 [http://127.0.0.1:8088/](http://127.0.0.1:8088/)。如需从客户内网访问，在本目录
创建 `.env` 并填写以下两项，将示例 IP 改成客户主机的内网 IP，然后重新执行启动命令。
程序本身不提供登录或 HTTPS。需要访问控制时，将其放在客户已有的认证网关之后。

```dotenv
CABLING_BIND_ADDRESS=192.168.1.20
CABLING_PORT=8088
```

### 离线交付

在联网且 CPU 架构与客户主机相同的机器上构建并导出镜像：

```bash
docker compose build
docker save -o standalone-cabling-1.0.tar standalone-cabling:1.0
```

将镜像包和本目录的 `compose.yaml` 一起交付。客户在 `compose.yaml` 所在目录执行：

```bash
docker load -i standalone-cabling-1.0.tar
docker compose up -d --no-build --pull never --wait
```

镜像标签可用 `CABLING_IMAGE` 覆盖。离线交付时，构建、导出和部署应使用同一标签。

## 直接在本机启动

在本目录使用 Python 3.11 或更新版本：

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

Windows 可用 `py` 替代 `python3`。程序会打开浏览器。如果未自动打开，复制终端显示的本地地址。保持终端运行，使用 Ctrl+C 退出。

## 使用

1. 编辑设备数量、面板物理口范围、设备名称前缀和型号。界面默认采用 256 台 Leaf、128 台 Spine、512 个计算柜。设备型号可留空。
2. 点击 **生成预览**，确认校验结果和两类链路示例。
3. 点击 **导出 Excel**，获取一张 `.xlsx`。每对子端口连接一行，表头冻结，支持按设备或链路类型筛选。线缆标签留空。

## 校验手工布线表

1. 点击顶部 **校验布线表**，选择与工作表对应的规则并填写拓扑参数。
2. 上传 XLSX 文件。首行使用导出模板的列名，Leaf 在左，对端在右。接口可写为 `65:1`，也可把物理口 `65` 和子端口 `1` 分在两列。工作表名称可留空，默认读取第一张。
3. 点击 **校验布线表**。逐行问题显示实际接法、预期接法和 Excel 行号。缺失链路显示建议补录的两端。可下载完整问题清单 CSV。

单段校验请上传对应的那一段。设备型号和线缆标签不影响布线端点的匹配。

## 固定规则

- Leaf–Spine 每个 Leaf 物理口的子端口 1、2 接第一台 Spine，3、4 接下一台。Spine 每个物理口接两台 Leaf，奇数 Leaf 接子端口 1、2，偶数 Leaf 接 3、4。每组按顺序配对。
- Leaf–计算柜每组计算柜共享相邻的一对 Leaf。柜侧奇数口接第一台，偶数口接第二台。每个柜连续占用各自 Leaf 的下联物理口，子端口 1–4 同号相接。

默认规模有 65,536 条上联和 65,536 条下联。详见 [需求](REQUIREMENTS.md) 和 [技术方案](DESIGN.md)。
