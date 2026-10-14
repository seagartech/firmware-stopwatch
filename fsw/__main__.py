import argparse
import socket
import threading
import webbrowser

from fsw import __version__
from fsw.config import load_config


def free_port(preferred):
    for port in [preferred, 0]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue


def main(argv=None):
    parser = argparse.ArgumentParser(prog="fsw", description="固件码表")
    parser.add_argument(
        "--version", action="version", version=f"firmware-stopwatch {__version__}"
    )
    parser.add_argument("--check", action="store_true", help="只检查设置，不打开网页")
    parser.add_argument("--port", type=int, default=8765, help="网页用的端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    print(f"firmware-stopwatch {__version__}")
    for problem in load_config().problems():
        print(f"· {problem}")
    if args.check:
        return

    import uvicorn

    from fsw.web.app import create_app

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    print(f"码表已启动，用浏览器打开：{url}")
    print("关掉这个窗口或按 Ctrl+C 就停止。")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
