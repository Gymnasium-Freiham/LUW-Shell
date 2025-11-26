import subprocess
import threading
import shutil
import shlex
import sys

def _reader_thread(pipe, write_fn):
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            write_fn(line.rstrip("\n"))
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass

def _run_stream(cmd_list, timeout=None):
    """
    Run command (list) and stream stdout/stderr live to sys.stdout/sys.stderr.
    Returns (returncode, exception_or_None)
    """
    try:
        proc = subprocess.Popen(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
    except Exception as e:
        return None, e

    t_out = threading.Thread(target=_reader_thread, args=(proc.stdout, lambda s: print(s)))
    t_err = threading.Thread(target=_reader_thread, args=(proc.stderr, lambda s: print(s, file=sys.stderr)))
    t_out.daemon = True
    t_err.daemon = True
    t_out.start()
    t_err.start()

    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as te:
        try:
            proc.kill()
        except Exception:
            pass
        return None, te

    # ensure readers finish
    t_out.join(timeout=1)
    t_err.join(timeout=1)
    return rc, None

def run_pwsh_stream(cmd_str, timeout=300):
    """
    Run PowerShell (pwsh or powershell) and stream output live.
    Returns (returncode, exception_or_None).
    """
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        return None, RuntimeError("pwsh/powershell not found")
    cmd = [exe, "-NoProfile", "-Command", cmd_str]
    return _run_stream(cmd, timeout=timeout)

def run_cmd_stream(cmd_str, timeout=None):
    """
    Run a shell command string (split with shlex) and stream output.
    Returns (returncode, exception_or_None).
    """
    try:
        parts = shlex.split(cmd_str)
    except Exception:
        parts = [cmd_str]
    return _run_stream(parts, timeout=timeout)
