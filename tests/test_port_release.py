"""码表自己开着的调试口要能一次关干净（10-20 真测：码表进程自己占着口没放，试测一直"打不开"）。"""
import sys
import types

from fsw.platforms.espidf import EspIdf


class FakeSerial:
    def __init__(self, port, baud, timeout=None):
        self.port, self.closed = port, False

    def close(self):
        self.closed = True


def test_release_ports_closes_everything_opened_through_open_port(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=FakeSerial))
    EspIdf._OPEN.clear()
    a, b = EspIdf.open_port("/dev/x"), EspIdf.open_port("/dev/x")
    EspIdf.close_port(a)                       # 正常关掉的不再留着
    assert a.closed and not b.closed and len(EspIdf._OPEN) == 1
    assert EspIdf.release_ports() == 1 and b.closed and not EspIdf._OPEN
    assert EspIdf.release_ports() == 0
