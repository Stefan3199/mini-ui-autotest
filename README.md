# mini-ui-autotest

[![test](https://github.com/Stefan3199/mini-ui-autotest/actions/workflows/test.yml/badge.svg)](https://github.com/Stefan3199/mini-ui-autotest/actions/workflows/test.yml)

一个从零实现的接口自动化测试框架（v0.4）。用例写在 YAML 里，跑一条命令，出 HTML 报告。

名字里的 ui 是给后续 UI 自动化留的位置，v0.4 仍只做接口测试。

## 特性

- **YAML 数据驱动**：新增用例只改 YAML，不动代码
- **多条件断言**：一条用例可同时判多个字段，支持 `eq`、`not_null`、`contains`、`gt`、`in`
- **状态码断言**：`status_code` 作为特殊字段，和响应体字段一起判
- **失败自动重试**：只重试环境抖动，抖动与真失败分账
- **请求头**：配置写公共头，用例可覆盖，报告里自动打码
- **超时控制**：配置默认超时，用例可覆盖，超时记 FLAKY 不误判 FAIL
- **标签筛选**：用例打 tags，命令行 `--tag` 只跑一组
- **HTML 报告**：总账、判决、预期与实际对比、请求详情、逐条断言结果
- **自动 CI**：push 或 PR 触发 GitHub Actions，跑测试并上传报告
- **分层结构**：用例加载、请求发送、断言、调度各管一摊

## 快速开始

要求 Python 3.10 及以上。

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

先起被测服务（仓库自带 demo）：

```bash
python demo/login_server.py
```

另开一个终端，跑测试：

```bash
python run.py
```

只跑冒烟组：

```bash
python run.py --tag smoke
```

打开 `report/report.html` 看报告。

## 配置

`config/config.yaml`：

```yaml
base_url: http://127.0.0.1:9300
report_dir: report
retry_count: 3
timeout: 5
headers:
  X-Token: default-token
```

- `base_url`：被测服务地址
- `report_dir`：报告输出目录（生成物，已加入 .gitignore）
- `retry_count`：单条用例最多尝试次数
- `timeout`：单次请求超时秒数，用例可覆盖
- `headers`：公共请求头，用例里的同名头会覆盖这里

## 用例格式

`case/login.yaml`：

```yaml
- name: 登录成功
  tags: [smoke, login]
  url: /login
  method: POST
  params:
    user: test001
    pwd: "right123"
  expect:
    - field: code
      op: eq
      value: 0
    - field: ok
      op: eq
      value: true
    - field: token
      op: not_null
```

字段说明：

- `name`：用例名，报告里显示
- `tags`：可选，标签列表，供 `--tag` 筛选
- `url`：接口路径，和 config.base_url 拼成完整地址
- `method`：GET 或 POST
- `headers`：可选，用例级请求头，同名覆盖配置
- `params`：请求参数
- `expect`：断言列表，全部命中才算 PASS

### 断言类型

- `eq`：字段值与 `value` 相等
- `not_null`：字段存在且不为 `None` 或空字符串
- `contains`：实际值包含 `value`，字符串看子串，列表看元素
- `gt`：实际值大于 `value`，只对数字
- `in`：实际值在 `value` 列表里

### 更多断言示例

```yaml
- name: 用户信息
  tags: [assertion]
  url: /user
  method: GET
  params: {}
  expect:
    - field: status_code
      op: eq
      value: 200
    - field: roles
      op: contains
      value: admin
    - field: age
      op: gt
      value: 18
    - field: name
      op: in
      value: ["test001", "guest"]
```

`status_code` 是特殊字段，判的是 HTTP 状态码；其他字段都从响应 JSON 里取。

## 报告

报告是单个 HTML 文件，包含：

- 顶部总账：共几条，过、挂、抖各几条，当前筛选的标签
- 每条用例：名字、判决、预期摘要、实际摘要
- 详情折叠：请求方法、地址、参数、请求头，失败原因，逐条断言结果

判决三态：

- `PASS`：全部断言命中
- `FAIL`：有断言没中
- `FLAKY`：重试次数用完仍没拿到响应，属于环境抖动，不算业务失败

10 条用例全绿时长这样：

![测试报告](docs/images/report.png)

## 持续集成

推送到 main 或开 PR 时，GitHub Actions 会自动：

1. 安装依赖
2. 启动 demo 被测服务
3. 跑全部用例
4. 上传 HTML 报告作为 artifact

用例有 `FAIL` 或 `FLAKY` 时，进程返回 1，流水线标红。报告在 Actions 运行页的 Artifacts 里下载。

工作流文件：`.github/workflows/test.yml`。

运行记录：

![GitHub Actions](docs/images/ci.png)

## 项目结构

```
mini-ui-autotest/
├── .github/
│   └── workflows/
│       └── test.yml       # GitHub Actions 工作流
├── case/                  # 用例
├── config/                # 配置
├── demo/                  # 被测 demo 服务
├── docs/
│   └── images/            # README 截图
├── engine/
│   ├── case_loader.py     # 读配置和用例，补齐 URL、请求头、超时
│   ├── request_sender.py  # 发请求，返回状态码和响应体
│   └── assertion.py       # 断言引擎
├── templates/
│   └── report.html        # 报告模板
├── run.py                 # 入口：筛选、调度、重试、记账、出报告
├── requirements.txt
├── README.md
└── LICENSE
```

## 设计说明

- 模块之间不互相 import，组装放在 `run.py`，避免包模式和脚本模式两套路径打架。
- `case_loader` 负责把用例加工成能直接发的样子：拼完整 URL，合并配置公共头和用例头，注入超时。
- `request_sender` 返回 `{"status_code": ..., "body": ...}`，断言层普通字段走 `body`，状态码走外层。
- 重试只服务环境抖动：`send_case` 拿不到响应时返回 `None`，`run.py` 负责重试；业务失败不重试。
- 判决定三态而不是两态：`FAIL` 是接口回了但断言没中，`FLAKY` 是根本没拿到响应。
- 报告里的参数和请求头在记账时脱敏：键名含 `pwd`、`pass`、`token`、`auth`、`key` 的值替换成 `***`。
- 断言先出逐条结果表，判决和报告摘要都从这张表派生。
- CI 看退出码不看屏幕输出，用例有挂或抖时 `run.py` 返回 1。

## Roadmap

- v0.2（已完成）：headers、timeout、标签筛选
- v0.3（已完成）：状态码断言、contains、gt、in
- v0.4（已完成）：GitHub Actions 持续集成
- 远期：Playwright UI 自动化

## License

MIT
