import pytest

from fsw.web.flow import Flow, FlowError


def statuses(flow):
    return {s.key: s.status for s in flow.steps}


def test_first_time_flow_has_four_steps_and_starts_at_the_first():
    flow = Flow.start("first")

    assert [s.name for s in flow.steps] == ["找关键步骤", "插计时点和烧录", "多轮实测", "报告"]
    assert statuses(flow) == {"identify": "doing", "probes": "todo", "measure": "todo", "report": "todo"}
    assert flow.viewing == "identify"


def test_retest_flow_has_five_steps_and_nothing_skipped_before_code_is_chosen():
    flow = Flow.start("retest")

    assert [s.name for s in flow.steps] == ["查改了什么", "找关键步骤", "插计时点和烧录", "多轮实测", "前后对比"]
    assert "skipped" not in statuses(flow).values()


def test_completing_a_step_moves_on_to_the_next():
    flow = Flow.start("first")

    flow.complete("identify")

    assert statuses(flow)["identify"] == "done"
    assert statuses(flow)["probes"] == "doing"
    assert flow.viewing == "probes"


def test_going_back_only_looks_and_keeps_later_work():
    flow = Flow.start("first")
    flow.complete("identify")
    flow.complete("probes")
    before = statuses(flow)

    flow.view("identify")

    assert flow.viewing == "identify"
    assert statuses(flow) == before
    assert flow.notice is None
    assert flow.current == "measure"  # 做到这里


def test_can_look_forward_again_after_looking_back():
    flow = Flow.start("first")
    flow.complete("identify")
    flow.complete("probes")
    flow.view("identify")

    flow.view("probes")

    assert flow.viewing == "probes"


def test_cannot_jump_to_a_step_not_reached_yet():
    flow = Flow.start("first")

    with pytest.raises(FlowError):
        flow.view("report")


def test_changing_an_earlier_step_resets_later_steps_and_says_so():
    flow = Flow.start("first")
    flow.complete("identify")
    flow.complete("probes")
    flow.view("identify")

    flow.change("identify")

    assert statuses(flow) == {"identify": "doing", "probes": "todo", "measure": "todo", "report": "todo"}
    assert flow.viewing == "identify"
    assert "插计时点和烧录" in flow.notice
    assert "重新做" in flow.notice


def test_changing_the_current_step_needs_no_notice():
    flow = Flow.start("first")

    flow.change("identify")

    assert flow.notice is None


def test_notice_goes_away_after_the_next_step_is_done():
    flow = Flow.start("first")
    flow.complete("identify")
    flow.change("identify")

    flow.complete("identify")

    assert flow.notice is None


def test_error_marks_the_step_red_and_stays_there():
    flow = Flow.start("first")
    flow.complete("identify")

    flow.fail("probes", "编译没通过")

    assert statuses(flow)["probes"] == "error"
    assert flow.error == "编译没通过"
    assert flow.viewing == "probes"
    assert statuses(flow)["identify"] == "done"


def test_retry_after_error_clears_red():
    flow = Flow.start("first")
    flow.fail("identify", "大模型没回应")

    flow.retry("identify")

    assert statuses(flow)["identify"] == "doing"
    assert flow.error is None


def test_retest_skips_steps_after_code_is_checked():
    flow = Flow.start("retest")

    flow.complete("diff", skip=["identify", "probes"])

    assert statuses(flow) == {
        "diff": "done", "identify": "skipped", "probes": "skipped",
        "measure": "doing", "compare": "todo",
    }
    with pytest.raises(FlowError):
        flow.view("identify")


def test_finishing_the_last_step_marks_the_flow_done():
    flow = Flow.start("first")
    for key in ["identify", "probes", "measure", "report"]:
        flow.complete(key)

    assert flow.finished
    assert flow.viewing == "report"


def test_only_the_current_step_can_be_completed():
    flow = Flow.start("first")

    with pytest.raises(FlowError):
        flow.complete("probes")
