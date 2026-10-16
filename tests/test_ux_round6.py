"""第 6 次亲手操作（全新环境，2026-10-16 20:26）发现的 4 处（决策 139）。"""
from tests.test_boss_short import JS, grab


def test_try_box_does_not_show_raw_device_lines():
    """试一次：不再把设备原始输出（比如 SystemInfo: free sram）摆在明面上，想看点"完整日志"。"""
    t = grab("tryBox")
    assert "设备最新一行" not in t and "fullLogBtn()" in t


def test_collecting_shows_only_the_stop_button():
    """测的时候只放"停止实测"，不放一个点不了的"查看报告"和"还差：还在测"。"""
    assert "blk('[data-col=\"stop\"]','还在测')" not in JS


def test_restore_keeps_saying_restoring_while_the_page_redraws():
    """写建议时页面会不停重画：还原中要记在状态里，不能一重画就变回"还原成原版"按钮。"""
    assert "restoring" in grab("restoreBox")
    h = JS[JS.index("const rs=e.target.closest('[data-restore]')"):][:900]
    assert "restoring=true" in h and "restoring=false" in h


def test_tiny_segments_are_drawn_as_one_block_with_separators():
    t = grab("timeline")
    assert "tiny" in t and 'stroke="var(--panel)"' in t


def test_start_collect_button_only_after_try_passes():
    """试通前不放"开始多轮实测"和"还差：烧录和试一次都完成后才能测"（决策 140）。"""
    assert "烧录和试一次都完成后才能测" not in JS
