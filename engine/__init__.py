"""mini-ui-autotest 引擎包。

分层：case_loader 读用例、runner 调度、report 出报告、cli 组装。
模块之间只允许单向依赖，组装放在 cli。
"""

__version__ = "1.1.0"
