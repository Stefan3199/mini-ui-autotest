# mini-ui-autotest

一个从零实现的接口自动化测试框架（v0.1）。用例写在 YAML 里，跑一条命令，出 HTML 报告。

名字里的 ui 是给后续 UI 自动化留的位置，v0.1 只做接口测试。

## 特性

- **YAML 数据驱动**：新增用例只改 YAML，不动代码
- **多条件断言**：一条用例可同时判多个字段，支持 `eq` 与 `not_null`
- **失败自动重试**：只重试环境抖动，抖动与真失败分账
- **HTML 报告**：总账、判决、预期与实际对比、请求详情、逐条断言结果
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

打开 `report/report.html` 看报告。

## 配置

`config/config.yaml`：

```yaml
base_url: http://127.0.0.1:9300
report_dir: report
retry_count: 3
```

- `base_url`：被测服务地址
- `report_dir`：报告输出目录（生成物，已加入 .gitignore）
- `retry_count`：单条用例最多尝试次数

## 用例格式

`case/login.yaml`：

```yaml
- name: 登录成功
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
- `url`：接口路径，和 `config.base_url` 拼成完整地址
- `method`：GET 或 POST
- `params`：请求参数
- `expect`：断言列表，全部命中才算 PASS

断言支持：

- `eq`：字段值与 `value` 相等
- `not_null`：字段存在且不为 `None` 或空字符串

## 报告

报告是单个 HTML 文件，包含：

- 顶部总账：共几条，过、挂、抖各几条
- 每条用例：名字、判决、预期摘要、实际摘要
- 详情折叠：请求方法、地址、参数，失败原因，逐条断言结果

判决三态：

- `PASS`：全部断言命中
- `FAIL`：有断言没中
- `FLAKY`：重试次数用完仍没拿到响应，属于环境抖动，不算业务失败

## 项目结构

```
mini-ui-autotest/
├── case/                  # 用例
├── config/                # 配置
├── demo/                  # 被测 demo 服务
├── engine/
│   ├── case_loader.py     # 读配置和用例
│   ├── request_sender.py  # 发请求
│   └── assertion.py       # 断言引擎
├── templates/
│   └── report.html        # 报告模板
├── run.py                 # 入口：调度、重试、记账、出报告
├── requirements.txt
├── README.md
└── LICENSE
```

## 设计说明

- 模块之间不互相 import，组装放在 `run.py`，避免包模式和脚本模式两套路径打架。
- 重试只服务环境抖动：`send_case` 拿不到响应时返回 `None`，`run.py` 负责重试；业务失败不重试。
- 报告里的请求参数在记账时脱敏，键名含 `pwd` 或 `pass` 的值替换成 `***`，避免明文密码进仓库。
- 断言先出逐条结果表，判决从表里派生，报告也直接读这张表。

## Roadmap

- v0.2：headers、timeout、用例分组
- 更多断言 op：`contains`、`gt`、`in`
- UI 自动化扩展（Playwright）

## License

MIT
