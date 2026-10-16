"""第 6 次亲手操作（全新环境，2026-10-16 20:26）发现的 4 处（决策 139）。"""
from tests.test_boss_short import JS, grab


def test_try_box_does_not_show_raw_device_lines():
    """试一次：不再把设备原始输出（比如 SystemInfo: free sram）摆在明面上，想看点"完整日志"。"""
    t = grab("tryBox")
    assert "设备最新一行" not in t and "fullLogBtn()" in t


def test_collecting_keeps_a_grey_report_button_with_clear_words():
    """测的时候"查看报告"照旧灰着显示（按钮统一规则，决策 141），提示字改成"测完才能看"，不再是别扭的"还在测"。"""
    assert "blk('[data-col=\"stop\"]','还在测')" not in JS
    assert JS.count("blk('[data-col=\"stop\"]','测完才能看')") == 2


def test_restore_keeps_saying_restoring_while_the_page_redraws():
    """写建议时页面会不停重画：还原中要记在状态里，不能一重画就变回"还原成原版"按钮。"""
    assert "restoring" in grab("restoreBox")
    h = JS[JS.index("const rs=e.target.closest('[data-restore]')"):][:900]
    assert "restoring=true" in h and "restoring=false" in h


def test_tiny_segments_are_drawn_as_one_block_with_separators():
    t = grab("timeline")
    assert "tiny" in t and 'stroke="var(--panel)"' in t
