"""调用系统自带的"选文件夹 / 选文件"窗口，返回选中的路径；取消返回 None。"""

import os
import subprocess
import sys


def _mac(kind):
    if kind == "dir":
        script = 'POSIX path of (choose folder with prompt "选固件源码文件夹")'
    else:
        script = 'POSIX path of (choose file with prompt "选固件源码压缩包（.zip / .tar.gz）")'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def _windows(kind):
    if kind == "dir":
        ps = ("Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description='选固件源码文件夹';if($d.ShowDialog() -eq 'OK'){$d.SelectedPath}")
    else:
        ps = ("Add-Type -AssemblyName System.Windows.Forms;$d=New-Object System.Windows.Forms.OpenFileDialog;"
              "$d.Filter='压缩包|*.zip;*.tar.gz;*.tgz';if($d.ShowDialog() -eq 'OK'){$d.FileName}")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True)
    return r.stdout.strip() or None


def _tk(kind):
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    try:
        if kind == "dir":
            return filedialog.askdirectory(title="选固件源码文件夹") or None
        return filedialog.askopenfilename(title="选固件源码压缩包") or None
    finally:
        root.destroy()


def native_pick(kind):
    if sys.platform == "darwin":
        return _mac(kind)
    if os.name == "nt":
        return _windows(kind)
    return _tk(kind)
