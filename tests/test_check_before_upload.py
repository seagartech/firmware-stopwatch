import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from check_before_upload import scan  # noqa: E402

# 拼出来的假值：不在源码里原样出现，repo 自己才能通过扫描
FAKE_KEY = "sk" + "-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
FAKE_PATH = "/" + "Users" + "/someone/esp/project"
FAKE_WIN_PATH = "C:" + "\\" + "Users" + "\\someone\\esp"


def make_repo(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def kinds(findings):
    return sorted({f.kind for f in findings})


def test_clean_folder_passes(tmp_path):
    make_repo(tmp_path, {"fsw/a.py": "print('hello')\n"})

    assert scan(tmp_path) == []


def test_fake_key_is_blocked(tmp_path):
    make_repo(tmp_path, {"fsw/a.py": f'KEY = "{FAKE_KEY}"\n'})

    findings = scan(tmp_path)

    assert kinds(findings) == ["密钥"]
    assert findings[0].file == "fsw/a.py"
    assert findings[0].line == 1


def test_report_never_prints_the_full_key(tmp_path):
    make_repo(tmp_path, {"fsw/a.py": f'KEY = "{FAKE_KEY}"\n'})

    assert FAKE_KEY not in scan(tmp_path)[0].snippet


def test_key_files_are_blocked_by_name(tmp_path):
    make_repo(tmp_path, {"deepseek_key.txt": "whatever\n", ".env": "X=1\n"})

    found = {f.file for f in scan(tmp_path) if f.kind == "密钥"}

    assert found == {"deepseek_key.txt", ".env"}


def test_local_paths_are_blocked(tmp_path):
    make_repo(
        tmp_path,
        {"a.py": f'P = "{FAKE_PATH}"\n', "b.md": f"放在 {FAKE_WIN_PATH} 下\n"},
    )

    assert {(f.file, f.kind) for f in scan(tmp_path)} == {
        ("a.py", "本机路径"),
        ("b.md", "本机路径"),
    }


def test_process_documents_are_blocked_by_path_and_by_mention(tmp_path):
    make_repo(
        tmp_path,
        {
            "milestones/01.md": "里程碑\n",
            "docs/decisions.md": "决策\n",
            "fsw/a.py": "# 理由见 ideas-backlog.md\n",
        },
    )

    found = {f.file for f in scan(tmp_path) if f.kind == "过程文档"}

    assert found == {"milestones/01.md", "docs/decisions.md", "fsw/a.py"}


def test_extra_private_words_come_from_local_list(tmp_path):
    make_repo(tmp_path, {"README.md": "作者：某某科技\n"})
    words = tmp_path.parent / "words.txt"
    words.write_text("# 不想公开的名字\n某某科技\n", encoding="utf-8")

    findings = scan(tmp_path, private_words_file=words)

    assert kinds(findings) == ["私人信息"]


def test_files_ignored_by_git_are_not_scanned(tmp_path):
    if not shutil.which("git"):
        return
    make_repo(
        tmp_path,
        {".gitignore": "secret.py\n", "secret.py": f'K = "{FAKE_KEY}"\n', "ok.py": "x = 1\n"},
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    assert scan(tmp_path) == []


def test_ignore_list_blocks_config_logs_and_build_output(tmp_path):
    shutil.copy(REPO / ".gitignore", tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    should_ignore = [
        ".env",
        "config.toml",
        "deepseek_key.txt",
        "claude_key.txt",
        "run.log",
        "build/app.bin",
        "dist/x.whl",
        ".venv/bin/python",
        "fsw/__pycache__/a.pyc",
    ]
    should_keep = ["config.example.toml", "fsw/a.py", "tests/logs/sample_round.txt"]

    def ignored(path):
        r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=tmp_path)
        return r.returncode == 0

    assert [p for p in should_ignore if not ignored(p)] == []
    assert [p for p in should_keep if ignored(p)] == []


def test_command_line_exit_code_and_plain_message(tmp_path):
    script = REPO / "tools" / "check_before_upload.py"
    make_repo(tmp_path, {"a.py": "x = 1\n"})
    ok = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True)
    assert ok.returncode == 0
    assert "没发现问题" in ok.stdout

    make_repo(tmp_path, {"a.py": f'KEY = "{FAKE_KEY}"\n'})
    bad = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True)
    assert bad.returncode == 1
    assert "a.py:1" in bad.stdout
    assert "先别上传" in bad.stdout
    assert FAKE_KEY not in bad.stdout


def test_this_repo_itself_passes():
    # 历史提交的作者邮箱取决于谁在用这份代码，这里只查当前文件；历史另有下面的测试
    assert scan(REPO, history=False) == []


ANON = "101267340+someone@users.noreply.github.com"


def commit(root, email, files, message="x"):
    make_repo(root, files)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=someone", "-c", f"user.email={email}", "commit", "-qm", message],
        cwd=root, check=True,
    )


def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_history_with_anonymous_email_passes(tmp_path):
    if not shutil.which("git"):
        return
    commit(git_repo(tmp_path), ANON, {"a.py": "x = 1\n"})

    assert scan(tmp_path) == []


def test_ai_noreply_email_in_history_passes(tmp_path):
    if not shutil.which("git"):
        return
    commit(git_repo(tmp_path), "noreply@anthropic.com", {"a.py": "x = 1\n"})

    assert scan(tmp_path) == []


def test_real_email_in_history_is_blocked_and_masked(tmp_path):
    if not shutil.which("git"):
        return
    commit(git_repo(tmp_path), "someone@example.com", {"a.py": "x = 1\n"})

    findings = scan(tmp_path)

    assert kinds(findings) == ["私人信息"]
    assert "someone@example.com" not in str(findings[0])


def test_key_removed_later_is_still_found_in_history(tmp_path):
    if not shutil.which("git"):
        return
    git_repo(tmp_path)
    commit(tmp_path, ANON, {"a.py": f'KEY = "{FAKE_KEY}"\n'})
    commit(tmp_path, ANON, {"a.py": "KEY = ''\n"})

    findings = scan(tmp_path)

    assert kinds(findings) == ["密钥"]
    assert "a.py" in findings[0].file
    assert FAKE_KEY not in str(findings[0])
    assert scan(tmp_path, history=False) == []


def test_local_path_and_private_word_in_history(tmp_path):
    if not shutil.which("git"):
        return
    git_repo(tmp_path)
    commit(tmp_path, ANON, {"a.md": f"放在 {FAKE_PATH}\n作者：某某科技\n"})
    commit(tmp_path, ANON, {"a.md": "干净了\n"})
    words = tmp_path.parent / "words.txt"
    words.write_text("某某科技\n", encoding="utf-8")

    assert kinds(scan(tmp_path, private_words_file=words)) == ["本机路径", "私人信息"]


def test_github_web_committer_noreply_passes(tmp_path):
    commit(git_repo(tmp_path), ANON, {"a.py": "x = 1\n"})
    subprocess.run(
        ["git", "-c", "user.name=GitHub", "-c", "user.email=noreply@github.com",
         "commit", "--amend", "--no-edit", "-q"], cwd=tmp_path, check=True,
    )
    assert scan(tmp_path) == []


def test_lookalike_github_email_is_still_blocked(tmp_path):
    commit(git_repo(tmp_path), "person-noreply@github.com", {"a.py": "x = 1\n"})
    findings = scan(tmp_path)
    assert kinds(findings) == ["私人信息"]
    assert "person-noreply@github.com" not in str(findings[0])
