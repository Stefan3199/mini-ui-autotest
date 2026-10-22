# 更新记录

## v1.0.1

v1.0.1 不新增功能，修 8 个跑起来才发现的问题。用例格式和命令行参数都没变。

### 修复

- **timeout 写错会让整轮崩掉**：`requests` 对非法 timeout 抛的是 `ValueError`，不在
  `RequestException` 里，异常穿到入口变成 traceback。现在用例和配置共用一套校验，
  写错在加载期报错，退出码 3。
- **敏感值从四个出口漏进报告**：断言明细的「实际」「期望」、「预期」摘要、「存变量」
  都没走脱敏。现在字段名最后一段命中敏感词就显示 `***`，上下文里仍是真值。
- **`case/` 子目录的用例不加载也不提示**：`glob` 改成 `rglob`，这类静默漏测最危险。
  错误信息改用相对路径，两个子目录下的同名文件能分清。
- **`--only-failed` 漏掉 SKIP**：上一轮上游挂、下游跳，重跑只跑上游，下游永远不验证。
  现在 `last-failed.json` 单独记 `skipped`，读的时候合并，不并进 `failed`。
- **列表里的字典不脱敏**：`params` 里放对象数组时，里面的 `token`、`pwd` 明文进报告。
  现在嵌套字典和列表都递归打码。
- **断言失败时 teardown 失败被吞**：主因仍是断言，但 reason 里会带上 teardown 失败。
- **非法正则静默判「没中」**：`regex: "("` 现在在加载期报错，并指出正则错在哪。
- **同一秒跑两次归档互相覆盖**：文件名加 `-2`、`-3` 后缀，不再覆盖。

### 测试

- 新增 `tests/test_v101.py`，回归覆盖上面 8 条
- 自测从 143 条增加到 160 条

## v1.0.0

v1.0 的主题是「判决可信、框架可测、接口可被机器消费」。没有大改 YAML 格式，
现有用例一行不用动。

### 修复

- **非 JSON 响应被当成环境抖动**：`requests` 的 `JSONDecodeError` 继承自
  `RequestException`，被 `send_case` 的 `except` 一起吞掉，返回 `None`，重试三次后
  记成 `FLAKY`「环境抖动」。真实故障（网关返回 HTML 错误页、接口返回空 body）
  因此永远查不出来，而且只要 body 不是 JSON，`status_code` 断言就永远拿不到响应。
  现在传输错误与协议错误分开抛，非 JSON 判 `FAIL`（原因「响应不是 JSON」），
  只涉及状态码的断言仍然有效。
- **参数化 + setup/teardown 重复拼 URL**：`expand_cases` 用浅拷贝，多行数据共享
  同一个 `setup` 列表，`prepare` 原地改 `url`，第二行会拼出
  `http://ahttp://a/echo`。现在展开用 `deepcopy`，`prepare` 改成返回新字典。
- **脱敏范围与文档不一致**：参数只打码 `pwd`/`pass`，`token`/`auth`/`key` 会明文
  进 HTML、JUnit 和归档。现在参数和请求头共用一份规则。
- **未知 op 静默判 FAIL**：`op` 写错（如 `eqq`）以前只在报告上写「期望 ?」，
  看起来像断言不中。现在加载期直接报错，并给出可用算子列表。
- **退出码混用**：配置写错和有失败都返回 1，CI 分不清该修谁。
- **重定向输出中文乱码**：Windows 下管道输出按 cp936 编码。

### 新增

- **判决原因（cause）**：`FAIL`/`FLAKY`/`SKIP` 都带原因（断言未通过、响应不是 JSON、
  连接失败、请求超时、传输失败、变量未定义、前置失败、后置失败、依赖未通过），
  报告和 `result.json` 都展示。
- **`report/result.json`**：机器可读结果，含每次请求尝试与逐条断言，供 CI、看板、
  脚本消费，不用解析 HTML。
- **语义化退出码**：0 全过 / 1 有 FAIL / 2 只有 FLAKY / 3 用例、配置或参数错误。
- **`group` 互斥组**：并发通道里同组用例排队执行，不同组照常并发。
- **`${ENV:NAME}`**：从环境变量取变量值；只用环境变量的用例不再强制串行。
- **`sensitive`**：配置级或用例级追加要打码的键名。
- **`--strict`**：用例里的未知字段从「提示」升级为「报错」。
- **`--keep`**：归档保留份数可配（配置项 `archive_keep`）。
- **`--open` / `--quiet` / `--verbose` / `--version`**。
- **请求方法扩展**：`PUT`、`PATCH`、`DELETE`、`HEAD`、`OPTIONS`（除 GET 类外都按
  JSON body 发送）。
- **`timeout` 支持 `[连接, 读取]`** 二元组。
- **报告**：原因列、按判决/名字筛选、每次请求尝试明细、断言算子列、通过率卡片。
- **框架自测**：`tests/` 下 140+ 条单元测试，覆盖断言、变量、脱敏、加载校验、
  请求发送（本地 stub 服务）、调度（假 sender）、报告产物、CLI 退出码和端到端。
- **CI 矩阵**：3.10 / 3.13 × Ubuntu / Windows；单测与端到端分两个任务。
- **demo 扩展**：`/html`（返回 HTML）和 `/broken`（半截 JSON）用来试非 JSON 判决；
  端口可通过命令行参数指定。

### 变化

- `run.py` 变成薄壳，逻辑移到 `engine/cli.py`；`engine` 成为正式包，带
  `__version__`。
- `engine/runner.py`、`engine/report.py`、`engine/result.py`、`engine/masking.py`
  是新拆出来的；`case_loader`、`request_sender`、`assertion`、`variables` 的对外
  行为保持兼容，内部重写。
- `send_case` 不再返回 `{"status_code", "body"}` 或 `None`，改为返回 `Response`
  或抛 `TransportError` / `ProtocolError`。直接调用它的外部代码需要跟着改。
- `load_cases` 返回 `Loaded(cases, warnings)` 而不是裸列表。

## v0.9

- 用例依赖 `depends_on`、`SKIP` 判决、通过率趋势图。

## v0.8

- JUnit 报告、失败重跑 `--rerun`、报告归档、`--list`。

## v0.7

- 外部数据文件 `data_file`、只跑失败用例 `--only-failed`、运行历史。

## v0.6

- 多环境 `--env`、`setup`/`teardown`、并发执行 `--workers`。

## v0.5

- 嵌套字段、`regex`/`len`/`type`、变量传递 `save`、参数化 `data`、耗时统计。

## v0.4

- GitHub Actions 持续集成。

## v0.3

- 状态码断言、`contains`、`gt`、`in`。

## v0.2

- `headers`、`timeout`、标签筛选 `--tag`。
