"""fixture：跨用例复用的前置资源。

和用例里的 setup 有什么区别？
- setup 是「这条用例自己的前置」，写在使用例里，每条都要重复一遍。
- fixture 是「一组用例共用的前置」，写一次，用例声明 `fixtures: [名字]` 就能用。

两种作用域：
- session：整轮只执行一次，值放进共享上下文，串行和并发两条通道都能读。
- case：每条引用它的用例各执行一次，值只进这条用例的上下文，用完跑 teardown。

失败处理：fixture 失败不抛异常，引用它的用例判 SKIP，原因写清楚。
理由是「前置没准备好」和「用例本身失败」是两回事，混在一起会让失败数虚高。
"""

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import yaml

from . import assertion, variables
from .runner import attempt_request

SCOPES = ("session", "case")
ALLOWED_KEYS = {"scope", "request", "save", "expect", "teardown", "depends_on"}
ALLOWED_STEP_KEYS = {"url", "method", "params", "headers", "timeout"}


class FixtureError(Exception):
    """fixture 文件写错了。cli 接住它，退出码 3。"""


@dataclass
class Fixture:
    """一个 fixture 的定义。request 已经拼好 URL、合好请求头。"""

    name: str
    scope: str = "case"
    request: dict = field(default_factory=dict)
    save: dict = field(default_factory=dict)
    expect: list = field(default_factory=list)
    teardown: list = field(default_factory=list)
    depends_on: list = field(default_factory=list)


# ---------------------------------------------------------------- 读取

def _read_yaml(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _check_step(spec, where):
    if not isinstance(spec, dict):
        raise FixtureError(f"{where}：request 要是字典")
    unknown = set(spec) - ALLOWED_STEP_KEYS
    if unknown:
        raise FixtureError(f"{where}：request 里有不认识的字段 {sorted(unknown)}")
    if not spec.get("url"):
        raise FixtureError(f"{where}：request 缺少 url")


def _normalize_teardown(spec, where):
    if spec is None:
        return []
    items = spec if isinstance(spec, list) else [spec]
    for item in items:
        _check_step(item, where)
    return items


def load_fixtures(path, config=None, default_headers=None, default_timeout=5):
    """读 fixture 文件。文件不存在就当没有 fixture，返回空字典。

    返回的 Fixture 里，request 和 teardown 已经拼成完整 URL，
    但 ${} 还没替换 —— 替换要等运行时有上下文。
    """
    from .case_loader import prepare_step

    path = Path(path)
    if not path.exists():
        return {}

    data = _read_yaml(path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FixtureError(f"{path.name} 顶层要是字典")
    raw = data.get("fixtures")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise FixtureError(f"{path.name} 里 fixtures 要是字典，键是 fixture 名字")

    fixtures = {}
    for name, spec in raw.items():
        where = f"{path.name} 的 {name}"
        if not isinstance(spec, dict):
            raise FixtureError(f"{where}：定义要是字典")
        unknown = set(spec) - ALLOWED_KEYS
        if unknown:
            raise FixtureError(f"{where}：不认识的字段 {sorted(unknown)}")

        scope = spec.get("scope") or "case"
        if scope not in SCOPES:
            raise FixtureError(f"{where}：scope 只能是 session 或 case")

        _check_step(spec.get("request"), where)

        save = spec.get("save") or {}
        if not isinstance(save, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in save.items()):
            raise FixtureError(f"{where}：save 要是「变量名: 字段路径」的字典")

        expect = spec.get("expect") or []
        if not isinstance(expect, list):
            raise FixtureError(f"{where}：expect 要是列表")
        for item in expect:
            problem = assertion.validate_item(item)
            if problem:
                raise FixtureError(f"{where}：{problem}")

        depends_on = spec.get("depends_on") or []
        if isinstance(depends_on, str):
            depends_on = [depends_on]
        if not isinstance(depends_on, list) or not all(
                isinstance(item, str) and item for item in depends_on):
            raise FixtureError(f"{where}：depends_on 要是名字或名字列表")

        request = prepare_step(spec["request"], config or {"base_url": ""},
                               default_headers, default_timeout)
        teardown = [prepare_step(step, config or {"base_url": ""},
                                 default_headers, default_timeout)
                    for step in _normalize_teardown(spec.get("teardown"), where)]

        fixtures[name] = Fixture(
            name=name, scope=scope, request=request, save=save,
            expect=expect, teardown=teardown, depends_on=list(depends_on),
        )

    _check_order(fixtures, path.name)
    return fixtures


def _check_order(fixtures, filename):
    """查依赖缺失和成环。加载期就报，别等跑到一半。"""
    for fixture in fixtures.values():
        for dep in fixture.depends_on:
            if dep not in fixtures:
                raise FixtureError(f"{filename} 的 {fixture.name}：依赖的 {dep} 不存在")
            if fixtures[dep].scope == "case" and fixture.scope == "session":
                raise FixtureError(
                    f"{filename} 的 {fixture.name}：session 作用域不能依赖 case 作用域的 {dep}")

    visiting, done = set(), set()

    def visit(name, path):
        if name in done:
            return
        if name in visiting:
            raise FixtureError(f"{filename}：fixture 依赖成环 → {' → '.join(path + [name])}")
        visiting.add(name)
        for dep in fixtures[name].depends_on:
            visit(dep, path + [name])
        visiting.discard(name)
        done.add(name)

    for name in fixtures:
        visit(name, [])


def order(names, fixtures):
    """按 depends_on 排 fixture 的执行顺序。"""
    result, seen = [], set()

    def visit(name):
        if name in seen:
            return
        seen.add(name)
        for dep in fixtures[name].depends_on:
            visit(dep)
        result.append(name)

    for name in names:
        visit(name)
    return result


# ---------------------------------------------------------------- 执行

class FixtureManager:
    """一轮运行里的 fixture 状态机。

    session fixture 在两条通道开跑之前执行一次；case fixture 在单条用例里执行。
    两种情况都记录失败原因，让调度层把相关用例判成 SKIP。
    """

    def __init__(self, fixtures=None, options=None, send=None):
        self.defs = dict(fixtures or {})
        self.options = options
        self.send = send
        self.context = {}
        self.failures = {}
        self._done = []
        self._lock = Lock()

    # -- 查询

    def has(self, name):
        return name in self.defs

    def names(self, scope=None):
        return [name for name, item in self.defs.items()
                if scope is None or item.scope == scope]

    def failure_reason(self, name):
        return self.failures.get(name)

    # -- session

    def prepare_session(self):
        """跑所有 session fixture。返回失败个数。重复调用不会重跑。"""
        for name in order(self.names("session"), self.defs):
            if name in self._done:
                continue
            self._execute(name, {}, session=True)
        return len(self.failures)

    # -- case

    def prepare_case(self, names, context):
        """跑这条用例声明的 case fixture。

        返回 (新的上下文, 失败描述)。失败描述非空时，调用方判 SKIP。
        session fixture 已经跑过就直接取共享值。
        """
        merged = dict(context)
        for name in order(names, self.defs):
            fixture = self.defs[name]
            if fixture.scope == "session":
                if name in self.failures:
                    return merged, f"fixture {name}：{self.failures[name]}"
                merged.update({k: v for k, v in self.context.items()
                               if k in self._saved_by(name)})
                continue
            values, reason = self._execute(name, merged, session=False)
            if reason:
                return merged, f"fixture {name}：{reason}"
            merged.update(values)
        return merged, None

    def teardown_case(self, names):
        """case 作用域的 fixture 按反序跑 teardown。返回失败描述。"""
        problems = []
        for name in reversed(list(names)):
            fixture = self.defs.get(name)
            if fixture is None or fixture.scope != "case":
                continue
            problem = self._teardown(fixture, self.context)
            if problem:
                problems.append(problem)
        return "；".join(problems) if problems else None

    def teardown_session(self):
        """整轮结束时，session fixture 按反序跑 teardown。返回失败描述。"""
        problems = []
        for name in reversed([n for n in self._done if n in self.defs]):
            fixture = self.defs[name]
            if fixture.scope != "session":
                continue
            problem = self._teardown(fixture, self.context)
            if problem:
                problems.append(problem)
        return "；".join(problems) if problems else None

    # -- 内部

    def _saved_by(self, name):
        """这个 fixture 会写进上下文的变量名。"""
        return set(self.defs[name].save)

    def _execute(self, name, context, session):
        fixture = self.defs[name]
        if name in self.failures:
            return {}, self.failures[name]

        base = {**self.context, **context}
        if fixture.depends_on:
            prepared, problem = self.prepare_case(
                [dep for dep in fixture.depends_on], base)
            if problem:
                self.failures[name] = f"依赖的 {problem}"
                return {}, self.failures[name]
            base = prepared

        try:
            request = variables.render(fixture.request, base)
        except variables.MissingVar as error:
            self.failures[name] = f"变量 ${{{error.name}}} 没定义"
            return {}, self.failures[name]

        response, _attempts, error = attempt_request(
            request, self.options, self.send, phase=f"fixture:{name}")

        if response is None:
            self.failures[name] = _error_text(error)
            return {}, self.failures[name]
        if response.status_code >= 400:
            self.failures[name] = f"HTTP {response.status_code}"
            return {}, self.failures[name]
        if fixture.expect:
            checks = assertion.run_checks(response.status_code, response.body, fixture.expect)
            bad = next((check for check in checks if not check.ok), None)
            if bad is not None:
                self.failures[name] = f"断言未通过：{assertion.describe_bad(bad)}"
                return {}, self.failures[name]
        if response.body_kind == "text" and fixture.save:
            self.failures[name] = "响应不是 JSON，取不到要保存的字段"
            return {}, self.failures[name]

        values = {}
        for variable, path in fixture.save.items():
            value = assertion.pick(response.body, path)
            if value is None:
                self.failures[name] = f"响应里取不到 {path}"
                return {}, self.failures[name]
            values[variable] = value

        if session:
            with self._lock:
                self.context.update(values)
                if name not in self._done:
                    self._done.append(name)
        return values, None

    def _teardown(self, fixture, context):
        problem = None
        for step in fixture.teardown:
            try:
                request = variables.render(step, {**self.context, **context})
            except variables.MissingVar as error:
                problem = problem or f"{fixture.name} teardown：变量 ${{{error.name}}} 没定义"
                continue
            response, _attempts, error = attempt_request(
                request, self.options, self.send, phase=f"fixture-teardown:{fixture.name}")
            if response is None:
                problem = problem or f"{fixture.name} teardown：{_error_text(error)}"
            elif response.status_code >= 400:
                problem = problem or f"{fixture.name} teardown：HTTP {response.status_code}"
        return problem


def _error_text(error):
    if error is None:
        return "没拿到响应"
    return getattr(error, "message", str(error))
