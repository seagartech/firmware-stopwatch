"""任务 13 测试用：门禁刷卡的假源码（关键行放在演示数据写的行号上）和录好的识别回复。"""

import json
from pathlib import Path

# 门禁刷卡演示数据里的 5 个计时点：文件、行号、那一行的代码
DOOR_LINES = {
    "main/rfid_task.c": {88: "    if (rc522_card_present(dev)) {", 131: "    uid_ok = rc522_read_uid(dev, &uid);"},
    "main/ui_led.c": {42: "    led_set(LED_GREEN); buzzer_beep(80);"},
    "main/net_auth.c": {207: "        auth_result = parse_auth_reply(resp);"},
    "main/lock_ctrl.c": {55: "    gpio_set_level(LOCK_PIN, 0);"},
}
DOOR_CMAKE = "cmake_minimum_required(VERSION 3.16)\ninclude($ENV{IDF_PATH}/tools/cmake/project.cmake)\nproject(door-ctrl)\n"


def door_source(root):
    """写一个门禁刷卡的假固件项目：关键行在演示数据写的行号上，其他行是注释。"""
    root = Path(root)
    for rel, lines in DOOR_LINES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        n = max(lines) + 20
        p.write_text("\n".join(lines.get(i, f"// {Path(rel).stem} 第 {i} 行") for i in range(1, n + 1)) + "\n", encoding="utf-8")
    (root / "CMakeLists.txt").write_text(DOOR_CMAKE, encoding="utf-8")
    (root / "sdkconfig.defaults").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")
    (root / "main/CMakeLists.txt").write_text('idf_component_register(SRCS "rfid_task.c" INCLUDE_DIRS ".")\n', encoding="utf-8")
    return root


DOOR_RESULT = {
    "summary": "这条线在设备上一共 5 步。开门要等服务器核对这张卡，这一段在设备外面；亮灯响一声在核对之前就发生了。",
    "start_found": True, "end_found": True, "missing": "",
    "start": {"name": "卡贴上去", "note": "卡贴近到设备察觉（每 0.1 秒查一次读卡芯片，推算 0～0.1 秒）"},
    "main": {"name": "开门这条", "end": "发出开锁"},
    "side": {"name": "亮灯这条", "end": "亮灯响一声", "found": "设备开门之前，会先亮灯响一声",
             "why": "亮灯响声是人最先得到的反馈。"},
    "steps": [
        {"id": "P1", "name": "读到卡", "file": "main/rfid_task.c", "line": 88, "line_of": "start", "sure": "高",
         "why": "读卡芯片报告有卡靠近，设备最早知道你刷卡的时刻", "doubt": ""},
        {"id": "P2", "name": "卡号读完", "file": "main/rfid_task.c", "line": 131, "line_of": "main", "sure": "高",
         "why": "卡号完整读出、校验通过；后面亮灯和发给服务器都从这里出发", "doubt": ""},
        {"id": "P3", "name": "亮灯响一声", "file": "main/ui_led.c", "line": 42, "line_of": "side", "sure": "中",
         "why": "调用亮绿灯和蜂鸣器，人在这一刻知道刷上了", "doubt": "亮灯交给另一个任务排队执行，灯真正亮起可能比这里晚一点"},
        {"id": "P4", "name": "收到服务器校验结果", "file": "main/net_auth.c", "line": 207, "line_of": "main", "sure": "高",
         "why": "服务器回复\"这张卡能不能开门\"", "doubt": ""},
        {"id": "P5", "name": "发出开锁", "file": "main/lock_ctrl.c", "line": 55, "line_of": "main", "sure": "中",
         "why": "给电磁锁断电，门弹开", "doubt": "代码里没找到门磁（门真正打开的信号），先用\"发出开锁\"的时刻代替"},
    ],
    "segments": [
        {"from": "P1", "to": "P2", "name": "读卡号", "who": "固件 + 读卡芯片", "outside": False},
        {"from": "P2", "to": "P4", "name": "服务器校验并返回", "who": "服务器 + 网络", "outside": True},
        {"from": "P4", "to": "P5", "name": "电磁锁动作", "who": "固件 + 门锁", "outside": False},
    ],
    "side_from": "P2",
    "side_segments": [
        {"from": "P1", "to": "P2", "name": "读卡号", "who": "固件 + 读卡芯片", "outside": False},
        {"from": "P2", "to": "P3", "name": "亮灯响一声", "who": "固件", "outside": False},
    ],
    "outside_note": "服务器：核对这张卡",
    # 第一次调用（理清流程）要的几项：和步骤写在同一个 json 里，假模型一份回复两次调用都能用
    "trigger": "刷卡",
    "mode": {"what": "无", "config": ""},
    "detect": {"kind": "读卡完成", "found": True, "looked": ""},
    "chain": [
        {"n": 1, "what": "读卡芯片报告有卡靠近", "file": "main/rfid_task.c", "line": 88, "code": "if (rc522_card_present(dev)) {",
         "branch": "", "repeat": "repeat", "after": ""},
        {"n": 2, "what": "卡号读完", "file": "main/rfid_task.c", "line": 131, "code": "uid_ok = rc522_read_uid(dev, &uid);",
         "branch": "", "repeat": "once", "after": "同一个任务里读到有卡之后接着读卡号"},
        {"n": 3, "what": "亮灯响一声", "file": "main/ui_led.c", "line": 42, "code": "led_set(LED_GREEN); buzzer_beep(80);",
         "branch": "", "repeat": "once", "after": "卡号读完后调用"},
        {"n": 4, "what": "收到服务器校验结果", "file": "main/net_auth.c", "line": 207, "code": "auth_result = parse_auth_reply(resp);",
         "branch": "", "repeat": "once", "after": "卡号发给服务器后，等它的回复"},
        {"n": 5, "what": "发出开锁", "file": "main/lock_ctrl.c", "line": 55, "code": "gpio_set_level(LOCK_PIN, 0);",
         "branch": "", "repeat": "once", "after": "校验通过后才开锁"},
    ],
    "doubts": [{"text": "门磁信号没接，开门只能量到\"发出开锁\"", "sure": "中"}],
}


def reply_for(result, found=True):
    """照提示词要求的样子：边找边报"找到：…"，最后一个 json 代码块。"""
    head = "".join(f"找到：{s['name']}（{s['file']}:{s['line']}）\n" for s in result.get("steps", [])) if found else ""
    return head + "读完了，结果如下。\n```json\n" + json.dumps(result, ensure_ascii=False, indent=1) + "\n```\n"


DOOR_REPLY = reply_for(DOOR_RESULT)
