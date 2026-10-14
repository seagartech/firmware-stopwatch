"""步骤条：测量中走到哪一步、哪些做完了。

规则：往回点只是回看，后面已做的不清掉；只有真改了东西（change）后面才变回未完成，
并给一句提示；出错的那一步标红，停在那里。
"""

from dataclasses import asdict, dataclass

KINDS = {
    "first": [
        ("identify", "找关键步骤"),
        ("probes", "插计时点和烧录"),
        ("measure", "多轮实测"),
        ("report", "报告"),
    ],
    "retest": [
        ("diff", "查改了什么"),
        ("identify", "找关键步骤"),
        ("probes", "插计时点和烧录"),
        ("measure", "多轮实测"),
        ("compare", "前后对比"),
    ],
}
REACHED = {"done", "doing", "error"}


class FlowError(Exception):
    pass


@dataclass
class Step:
    key: str
    name: str
    status: str = "todo"  # todo / doing / done / error / skipped


@dataclass
class Flow:
    kind: str
    steps: list
    viewing: str
    notice: str | None = None
    error: str | None = None

    @classmethod
    def start(cls, kind):
        if kind not in KINDS:
            raise FlowError(f"不认识的测量方式：{kind}")
        steps = [Step(k, n) for k, n in KINDS[kind]]
        steps[0].status = "doing"
        return cls(kind=kind, steps=steps, viewing=steps[0].key)

    def _step(self, key):
        for s in self.steps:
            if s.key == key:
                return s
        raise FlowError(f"没有这一步：{key}")

    def _after(self, key):
        i = [s.key for s in self.steps].index(key)
        return [s for s in self.steps[i + 1:] if s.status != "skipped"]

    @property
    def current(self):
        """做到这里：正在做或出错的那一步；都做完了就是 None。"""
        for s in self.steps:
            if s.status in ("doing", "error"):
                return s.key
        return None

    @property
    def finished(self):
        return all(s.status in ("done", "skipped") for s in self.steps)

    def view(self, key):
        step = self._step(key)
        if step.status not in REACHED:
            what = "跳过了" if step.status == "skipped" else "还没做到"
            raise FlowError(f"「{step.name}」{what}，不能打开")
        self.viewing = key

    def complete(self, key, skip=()):
        step = self._step(key)
        if step.status != "doing":
            raise FlowError(f"「{step.name}」现在不是正在做的那一步")
        step.status = "done"
        self.notice = None
        for k in skip:
            self._step(k).status = "skipped"
        later = self._after(key)
        if later:
            later[0].status = "doing"
            self.viewing = later[0].key

    def change(self, key):
        step = self._step(key)
        if step.status not in REACHED:
            raise FlowError(f"「{step.name}」还没做到，不能改")
        reset = [s for s in self._after(key) if s.status in REACHED]
        for s in reset:
            s.status = "todo"
        step.status = "doing"
        self.error = None
        self.viewing = key
        self.notice = (
            f"你改了「{step.name}」，后面的"
            + "、".join(f"「{s.name}」" for s in reset)
            + "要重新做"
            if reset
            else None
        )

    def fail(self, key, message):
        step = self._step(key)
        if step.status not in ("doing", "error"):
            raise FlowError(f"「{step.name}」现在不是正在做的那一步")
        step.status = "error"
        self.error = message
        self.viewing = key

    def retry(self, key):
        step = self._step(key)
        if step.status != "error":
            raise FlowError(f"「{step.name}」没有出错")
        step.status = "doing"
        self.error = None

    def to_dict(self):
        d = asdict(self)
        d["current"] = self.current
        d["finished"] = self.finished
        return d
