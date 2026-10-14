"""平台适配层：编译、烧录、读调试口三个动作，每个平台一个实现，换平台不动别处。

第一版只有乐鑫（ESP-IDF）；STM32、Pico 以后照同样的样子加一个实现、登记到 PLATFORMS 就行。
每个实现要有：
  build(源码, target, board, on_progress) → {"ok", "seconds", "error", "log"}
  flash(源码, port, on_waiting, on_progress) → {"ok", "port", "seconds", "error"}
  read_lines(串口) → 一行行设备输出
"""


def get_platform(name):
    from fsw.platforms.espidf import EspIdf
    platforms = {"ESP-IDF": EspIdf}
    return platforms[name]
