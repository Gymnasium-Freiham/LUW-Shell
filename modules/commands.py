import math
import ast
import operator
import datetime
import os
import re
import platform
import shutil
import random
import json
import base64
import urllib.request
import stat
import io
import contextlib
import subprocess
import sys
import time
import shlex
from . import colors as colors_mod
import html  # NEW: for unescaping HTML entities in fetched web content


def useHelper(helper):
    exe_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "helpers",
        helper
    )

    result = subprocess.run(
        [exe_path],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace"
    )

    return result.stdout


def pwd(args: dict) -> str:
    """Gibt das aktuelle Arbeitsverzeichnis zurück."""
    return os.getcwd()


def cd(args: dict) -> str:
    """Wechselt das Arbeitsverzeichnis. Unterstützt --mkdir/--create/--p zum Anlegen fehlender Verzeichnisse."""
    raw = args.get("str", "") or ""
    # expand env vars and user (~)
    path = _expand_env(raw)
    path = os.path.expanduser(path)
    path = os.path.expandvars(path)

    if not path:
        # no argument -> go home
        try:
            home = os.path.expanduser("~")
            os.chdir(home)
            return os.getcwd()
        except Exception as e:
            return f"cd failed: {e}"

    # make absolute, relative paths resolved against current working dir
    abs_path = path if os.path.isabs(path) else os.path.abspath(
        os.path.join(os.getcwd(), path))
    abs_path = os.path.normpath(abs_path)

    # check for mkdir/create flags (accept "1","true","yes")
    def _truthy(v):
        if v is None:
            return False
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    want_mkdir = _truthy(args.get("mkdir")) or _truthy(
        args.get("create")) or _truthy(args.get("p"))

    if not os.path.exists(abs_path):
        if want_mkdir:
            try:
                os.makedirs(abs_path, exist_ok=True)
            except Exception as e:
                return f"cd failed creating {abs_path}: {e}"
        else:
            return f"cd failed: path not found: {abs_path}"

    try:
        os.chdir(abs_path)
        return os.getcwd()
    except Exception as e:
        return f"cd failed: {e}"


def whoami(args: dict) -> str:
    """Gibt den aktuellen Benutzer zurück."""
    return os.getlogin()


def sysinfo(args: dict) -> str:
    """Systeminformationen anzeigen."""
    return f"{platform.system()} {platform.release()} ({platform.version()})"


def disk_usage(args: dict) -> str:
    """Zeigt freien und belegten Speicherplatz an."""
    path = args.get("str", ".")
    try:
        usage = shutil.disk_usage(path)
        return f"Total: {usage.total} | Used: {usage.used} | Free: {usage.free}"
    except Exception as e:
        return f"disk_usage failed: {e}"


def rand(args: dict) -> str:
    """Zufallszahl generieren."""
    start = int(args.get("start", 0))
    end = int(args.get("end", 100))
    return str(random.randint(start, end))


def grep(args: dict) -> str:
    """Einfache Textsuche in einer Datei."""
    pattern = args.get("pattern", "")
    file = args.get("file", "")
    if not pattern or not file:
        return "Fehler: grep benötigt --pattern und --file"
    try:
        with open(file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        matches = [line.strip() for line in lines if re.search(pattern, line)]
        return "\n".join(matches) if matches else "Keine Treffer"
    except Exception as e:
        return f"grep failed: {e}"


def reverse(args: dict) -> str:
    """
    Reverse text. Supports:
    - positional text: reverse Hello -> OLLEH
    - --file <path>: reverse contents of file
    - --url <url>: fetch URL (first 64KB) and reverse body text
    """
    # file takes precedence, then url, then positional str
    file_path = args.get("file") or args.get("f")
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as e:
            return f"reverse failed reading file: {e}"
    else:
        url = args.get("url") or args.get("u")
        if url:
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = resp.read(1024 * 64)
                    text = data.decode(errors="replace")
            except Exception as e:
                return f"reverse failed fetching url: {e}"
        else:
            text = args.get("str", "") or ""

    # ensure string and perform reverse
    try:
        return str(text)[::-1]
    except Exception as e:
        return f"reverse failed: {e}"


def upper(args: dict) -> str:
    text = args.get("str", "")
    return text.upper()


# safe calculator that only allows numeric arithmetic
_allowed_ops = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
    # Treat ^ as exponentiation (common calculator expectation)
    ast.BitXor: operator.pow,
}

_unary_ops = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node):
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_type = type(node.op)
        # prevent extremely large exponents
        if op_type in (ast.Pow, ast.BitXor):
            if isinstance(right, (int, float)):
                # limit integer exponent magnitude to avoid excessive computation
                if isinstance(right, int) and abs(right) > 10000:
                    raise ValueError("exponent too large")
        if op_type in _allowed_ops:
            return _allowed_ops[op_type](left, right)
        raise ValueError("unsupported operator")
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type in _unary_ops:
            return _unary_ops[op_type](_eval_node(node.operand))
        raise ValueError("unsupported unary operator")
    if isinstance(node, ast.Constant):  # Python 3.8+
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("only numbers allowed")
    if isinstance(node, ast.Num):  # fallback older nodes
        return node.n
    raise ValueError("unsupported expression element")


def calculator(args: dict) -> str:
    exe_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "helpers",
        "calc.exe"
    )

    result = subprocess.run(
        [exe_path, "--input", args["str"]],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace"
    )

    return result.stdout


def datetime_now(args: dict) -> str:
    # return an ISO-formatted string instead of a datetime object
    return useHelper("time.exe")


_env_pattern = re.compile(
    r'\$env:([A-Za-z_]\w*)|\${([A-Za-z_]\w*)}|\$([A-Za-z_]\w*)')


def _expand_env(s: str) -> str:
    if not s:
        return s

    def repl(m):
        name = m.group(1) or m.group(2) or m.group(3)
        return os.environ.get(name, "")
    return _env_pattern.sub(repl, s)


def echo(args: dict) -> str:
    # expand environment variables inside the string
    return _expand_env(args.get("str", ""))


def ls(args: dict) -> str:
    path = _expand_env(args.get("str", "")) or args.get("path", ".")
    try:
        entries = sorted(os.listdir(path))
        return "\n".join(entries)
    except Exception as e:
        return f"ls failed: {e}"


def cat(args: dict) -> str:
    path = args.get("str", "")
    if not path:
        return "usage: cat <file>"
    out = []
    for p in path.split():
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                out.append(f.read())
        except Exception as e:
            out.append(f"cat failed ({p}): {e}")
    return "\n".join(out)


def head(args: dict) -> str:
    path = args.get("str", "")
    n = int(args.get("n", 10))
    if not path:
        return "usage: head <file> [--n N]"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for _ in range(n):
                l = f.readline()
                if not l:
                    break
                lines.append(l.rstrip("\n"))
        return "\n".join(lines)
    except Exception as e:
        return f"head failed: {e}"


def tail(args: dict) -> str:
    path = args.get("str", "")
    n = int(args.get("n", 10))
    if not path:
        return "usage: tail <file> [--n N]"
    try:
        with open(path, "rb") as f:
            # efficient small-file tail
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 1024
            data = b""
            lines = []
            while size > 0 and len(lines) <= n:
                read_size = min(block, size)
                f.seek(size - read_size, os.SEEK_SET)
                chunk = f.read(read_size) + data
                data = chunk
                lines = data.splitlines()
                size -= read_size
            text_lines = [ln.decode(errors="replace") for ln in lines[-n:]]
            return "\n".join(text_lines)
    except Exception as e:
        return f"tail failed: {e}"


def cp(args: dict) -> str:
    src = args.get("src") or args.get("str", "")
    dst = args.get("dst") or args.get("to")
    if not src or not dst:
        return "usage: cp --src <source> --dst <dest>  OR  cp <src> <dst>"
    try:
        shutil.copy2(src, dst)
        return ""
    except Exception as e:
        return f"cp failed: {e}"


def mv(args: dict) -> str:
    src = args.get("src") or args.get("str", "")
    dst = args.get("dst") or args.get("to")
    if not src or not dst:
        return "usage: mv --src <source> --dst <dest>  OR  mv <src> <dst>"
    try:
        shutil.move(src, dst)
        return ""
    except Exception as e:
        return f"mv failed: {e}"


def rm(args: dict) -> str:
    path = args.get("str", "") or args.get("path", "")
    recursive = str(args.get("r", args.get("recursive", ""))
                    ).strip().lower() in ("1", "true", "yes", "on")
    if not path:
        return "usage: rm <path> [--r 1]"
    try:
        if os.path.isdir(path) and recursive:
            shutil.rmtree(path)
        else:
            os.remove(path)
        return ""
    except Exception as e:
        return f"rm failed: {e}"


def mkdir_cmd(args: dict) -> str:
    path = args.get("str", "") or args.get("path", "")
    if not path:
        return "usage: mkdir <path>"
    try:
        os.makedirs(path, exist_ok=True)
        return ""
    except Exception as e:
        return f"mkdir failed: {e}"


def touch(args: dict) -> str:
    path = args.get("str", "") or args.get("path", "")
    if not path:
        return "usage: touch <path>"
    try:
        with open(path, "a", encoding="utf-8"):
            os.utime(path, None)
        return ""
    except Exception as e:
        return f"touch failed: {e}"


def file_info(args: dict) -> str:
    path = args.get("str", "") or args.get("path", "")
    if not path:
        return "usage: stat|info <path>"
    try:
        st = os.stat(path)
        mode = stat.filemode(st.st_mode)
        return f"{path}\nsize={st.st_size} bytes\nmode={mode}\nmtime={datetime.datetime.fromtimestamp(st.st_mtime).isoformat()}"
    except Exception as e:
        return f"stat failed: {e}"


def wc(args: dict) -> str:
    path = args.get("str", "") or args.get("path", "")
    if not path:
        return "usage: wc <file>"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        lines = text.count("\n")
        words = len(text.split())
        bytes_len = len(text.encode("utf-8"))
        return f"{lines} {words} {bytes_len} {path}"
    except Exception as e:
        return f"wc failed: {e}"


def json_pretty(args: dict) -> str:
    s = args.get("str", "") or ""
    if not s:
        return "usage: json '<json>' OR json --file <path>"
    # if file given
    if args.get("file"):
        try:
            with open(args["file"], "r", encoding="utf-8") as f:
                obj = json.load(f)
            return json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"json failed: {e}"
    try:
        obj = json.loads(s)
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"json failed: {e}"


def http_get(args: dict) -> str:
    url = args.get("str", "") or args.get("url", "")
    if not url:
        return "usage: http_get <url>"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read(1024 * 64)
            # show headers and up to 64KB body
            hdrs = dict(resp.getheaders())
            body = data.decode(errors="replace")
            return f"Status: {resp.status}\nHeaders: {hdrs}\n\n{body}"
    except Exception as e:
        return f"http_get failed: {e}"


def b64_encode(args: dict) -> str:
    s = args.get("str", "") or ""
    if not s:
        return "usage: b64_encode <text>"
    try:
        return base64.b64encode(s.encode("utf-8")).decode("ascii")
    except Exception as e:
        return f"b64_encode failed: {e}"


def b64_decode(args: dict) -> str:
    s = args.get("str", "") or ""
    if not s:
        return "usage: b64_decode <b64>"
    try:
        return base64.b64decode(s.encode("ascii"), validate=False).decode("utf-8", errors="replace")
    except Exception as e:
        return f"b64_decode failed: {e}"


def find(args: dict) -> str:
    path = args.get("str", "") or args.get("path", ".")
    name = args.get("name")
    if not name:
        return "usage: find <path> --name <pattern>"
    matches = []
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                if name in f:
                    matches.append(os.path.join(root, f))
        return "\n".join(matches) if matches else "No matches"
    except Exception as e:
        return f"find failed: {e}"

# Add fallback text wrap + ascii animals (used when cowsay package not present or returns None)


def _wrap_text(msg: str, width: int = 40):
    words = msg.split()
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


_ANIMALS = {
    "cow": r"""
        \   ^__^
         \  (oo)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
    """,
    "tux": r"""
        \   .--.
         \ (o_o )
           ( ) )
            " "
    """,
    "dragon": r"""
        \                    / \  //\
         \    |\___/|      /   \//  \\
              /O  O  \__  /    //  | \ \    
             /     /  \/_/    //   |  \  \  
             @___@'   \/_   //    |   \   \ 
    """,
    "sheep": r"""
        \     __
         \  (oo)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
    """,
    "bunny": r"""
        \ (\_/)
         \( . .)
         (  ---)
          | | |
    """,
}

# --- NEW: load .cow files from project 'cows' directory and register by filename (kitty.cow -> --animal kitty)
_COW_TEMPLATES = {}
try:
    _PROJECT_ROOT = os.path.dirname(
        os.path.dirname(__file__))  # parent of modules/
    _COW_DIR = os.path.join(_PROJECT_ROOT, "cows")
    if os.path.isdir(_COW_DIR):
        for _fn in sorted(os.listdir(_COW_DIR)):
            if _fn.lower().endswith(".cow"):
                _name = os.path.splitext(_fn)[0].lower()
                try:
                    with open(os.path.join(_COW_DIR, _fn), "r", encoding="utf-8", errors="replace") as _f:
                        _COW_TEMPLATES[_name] = _f.read()
                except Exception:
                    # ignore unreadable cow files
                    pass
except Exception:
    _COW_TEMPLATES = {}

# prefer external cowsay if available; be robust to functions that print instead of returning
try:
    import cowsay as _cowsay_mod  # optional external dependency
    _HAVE_COWSAY = True
except Exception:
    _cowsay_mod = None
    _HAVE_COWSAY = False

_COLOR_MAP = {
    "black": "\033[30m", "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m", "white": "\033[37m",
    "bright_black": "\033[90m", "bright_red": "\033[91m", "bright_green": "\033[92m",
    "bright_yellow": "\033[93m", "bright_blue": "\033[94m", "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m", "bright_white": "\033[97m",
}


def _build_fallback_cowsay(msg: str, animal: str):
    lines = _wrap_text(msg, width=40)
    maxlen = max(len(l) for l in lines)
    if len(lines) == 1:
        top = " " + "_" * (maxlen + 2)
        middle = f"< {lines[0].ljust(maxlen)} >"
        bottom = " " + "-" * (maxlen + 2)
        bubble = "\n".join([top, middle, bottom])
    else:
        top = " " + "_" * (maxlen + 2)
        bottom = " " + "-" * (maxlen + 2)
        mid_lines = [f"| {l.ljust(maxlen)} |" for l in lines]
        bubble = "\n".join([top] + mid_lines + [bottom])

    art = _ANIMALS.get(animal, _ANIMALS["cow"])
    return bubble + "\n" + art


def cowsay(args: dict) -> str:
    """
    cowsay --animal [ANIMAL] --colour [COLOUR] --rgb R G B --cowfile <file> <message>
    Use cowsay package when available; capture printed output if it prints instead of returning;
    fallback to internal ASCII art when necessary. Apply ANSI color or RGB color if requested.
    If --cowfile <file> is given, use the .cow file as the cow template (if supported).
    """
    msg = str(args.get("str", "")).strip()

    animal = str(args.get("animal", args.get("a", "cow") or "cow")).lower()
    colour = args.get("colour", args.get("color", args.get("colour", None)))
    if isinstance(colour, str):
        colour = colour.lower()
    else:
        colour = None

    # --- NEW: prefer explicit --cowfile, else check loaded cow templates by animal name ---
    cowfile = args.get("cowfile")
    cowfile_content = None
    if cowfile:
        try:
            with open(cowfile, "r", encoding="utf-8", errors="replace") as f:
                cowfile_content = f.read()
        except Exception as e:
            return f"cowsay failed to read cowfile: {e}"
    else:
        # if user requested an animal that matches a .cow filename, use that template
        if animal and animal in _COW_TEMPLATES:
            cowfile_content = _COW_TEMPLATES[animal]

    # RGB color support: accept --rgb "R G B" or --rgb R and the next two numeric tokens at start of message
    rgb_tuple = None
    raw_rgb = args.get("rgb")
    if raw_rgb is not None:
        parts = []
        if isinstance(raw_rgb, str):
            parts = raw_rgb.strip().split()
        elif isinstance(raw_rgb, (list, tuple)):
            parts = list(raw_rgb)
        elif isinstance(raw_rgb, int):
            parts = [str(raw_rgb)]
        nums = []
        for p in parts:
            try:
                n = int(str(p))
                nums.append(n)
            except Exception:
                pass
        if len(nums) < 3 and msg:
            msg_tokens = msg.split()
            taken = []
            while len(nums) + len(taken) < 3 and msg_tokens:
                t = msg_tokens[0]
                try:
                    n = int(t)
                    taken.append(n)
                    msg_tokens.pop(0)
                except Exception:
                    break
            if taken:
                nums.extend(taken)
                msg = " ".join(msg_tokens)
        if len(nums) >= 3:
            r = max(0, min(255, nums[0]))
            g = max(0, min(255, nums[1]))
            b = max(0, min(255, nums[2]))
            rgb_tuple = (r, g, b)
        else:
            rgb_tuple = None

    art = None

    if _HAVE_COWSAY and _cowsay_mod is not None:
        try:
            buf = io.StringIO()
            # --- NEW: .cow file support with cowsay lib ---
            if cowfile_content:
                # cowsay.cowsay accepts a cow parameter as a function or a string
                # We'll use the cowsay.read_dot_cow function if available, else fallback to passing the string
                cowfunc = None
                if hasattr(_cowsay_mod, "read_dot_cow"):
                    try:
                        cowfunc = _cowsay_mod.read_dot_cow(cowfile_content)
                    except Exception:
                        cowfunc = None
                # fallback: pass the string directly (some cowsay versions accept this)
                cowparam = cowfunc if cowfunc else cowfile_content
                with contextlib.redirect_stdout(buf):
                    try:
                        ret = _cowsay_mod.cowsay(msg, cow=cowparam)
                    except Exception:
                        ret = _cowsay_mod.cowsay(msg)
                out = ret if isinstance(ret, str) and ret else buf.getvalue()
                art = out if out else None
            else:
                func = getattr(_cowsay_mod, animal, None)
                if callable(func):
                    with contextlib.redirect_stdout(buf):
                        ret = func(msg)
                    out = ret if isinstance(
                        ret, str) and ret else buf.getvalue()
                    art = out if out else None
                else:
                    with contextlib.redirect_stdout(buf):
                        try:
                            ret = _cowsay_mod.cowsay(msg, cow=animal)
                        except TypeError:
                            ret = _cowsay_mod.cowsay(msg)
                    out = ret if isinstance(
                        ret, str) and ret else buf.getvalue()
                    art = out if out else None
        except Exception:
            art = None

    # If external cowsay failed or returned None, use fallback
    if not art:
        if cowfile_content:
            # Build the speech bubble
            lines = _wrap_text(msg, width=40)
            maxlen = max((len(l) for l in lines), default=0)
            if len(lines) == 1:
                top = " " + "_" * (maxlen + 2)
                middle = f"< {lines[0].ljust(maxlen)} >"
                bottom = " " + "-" * (maxlen + 2)
                bubble = "\n".join([top, middle, bottom])
            else:
                top = " " + "_" * (maxlen + 2)
                bottom = " " + "-" * (maxlen + 2)
                mid_lines = [f"| {l.ljust(maxlen)} |" for l in lines]
                bubble = "\n".join([top] + mid_lines + [bottom])
            # Try to render the .cow template nicely
            rendered_cow = _render_dot_cow(
                cowfile_content, msg, eyes=None, tongue=None)
            if rendered_cow:
                art = bubble + "\n" + rendered_cow
            else:
                # last-resort: show bubble then raw cow content (previous behavior)
                art = bubble + "\n" + cowfile_content
        else:
            art = _build_fallback_cowsay(msg, animal)

    color_prefix = ""
    color_suffix = colors_mod.RESET if hasattr(
        colors_mod, "RESET") else "\033[0m"

    if rgb_tuple and len(rgb_tuple) == 3:
        r, g, b = rgb_tuple
        color_prefix = f"\033[38;2;{r};{g};{b}m"
    elif colour and colour in _COLOR_MAP:
        color_prefix = _COLOR_MAP[colour]
    elif colour and colour in ("yellow", "grey", "light_green"):
        if colour == "yellow":
            color_prefix = getattr(colors_mod, "YELLOW", "")
        if colour == "grey":
            color_prefix = getattr(colors_mod, "GREY", "")
        if colour == "light_green":
            color_prefix = getattr(colors_mod, "LIGHT_GREEN", "")

    if color_prefix:
        colored = "\n".join(
            f"{color_prefix}{line}{color_suffix}" for line in str(art).splitlines())
        return colored
    return str(art)


def _detect_linux_distro():
    """Return distro id (lowercased) and pretty name if available, else (None, None)."""
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            data = f.read()
        info = {}
        for line in data.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                info[k.strip()] = v
        distro_id = info.get("ID", "").lower()
        name = info.get("NAME", "")
        return distro_id, name
    except Exception:
        return None, None


# Logos (small, single-character/shape recognizable)
_LOGOS = {
    "windows": [
        " ███ ███      ",
        "              ",
        " ███ ███      ",
    ],
    "macos": [
        "   .:;:.",
        "  /  .  \\",
        " |  ( ) |",
        "  \\__|_/",
    ],
    "ubuntu": [
        "   .----.",
        "  /  ()  \\",
        " |  (__) |",
        "  \\____/ ",
    ],
    "tux": [
        "   _==_",
        "  (o_o)",
        "  /( )\\",
        "   \" \" ",
    ],
    "chromium": [
        "   _____",
        "  / ___ \\",
        " | |   | |",
        "  \\_____/ ",
    ],
}


def os_type(args: dict) -> str:
    """
    os-type
    Shows OS/specs with a platform-specific colorful ASCII logo at left.
    """
    try:
        system = platform.system()
    except Exception:
        system = "Unknown"

    distro_id = None
    pretty_name = ""
    if system.lower() == "linux":
        # e.g. 'ubuntu', 'chromium', etc.
        distro_id, pretty_name = _detect_linux_distro()

    # choose logo key
    key = None
    if system.lower().startswith("win"):
        key = "windows"
    elif system.lower().startswith("darwin") or system.lower().startswith("mac"):
        key = "macos"
    elif system.lower() == "linux":
        if distro_id and "ubuntu" in distro_id:
            key = "ubuntu"
        elif distro_id and ("chromium" in distro_id or "chromiumos" in distro_id):
            key = "chromium"
        else:
            key = "tux"
    else:
        key = "tux"

    logo_lines = _LOGOS.get(key, _LOGOS["tux"])

    # collect system info lines
    try:
        uname = platform.uname()
        node = uname.node
        release = uname.release
        version = uname.version
        machine = uname.machine
        processor = uname.processor or platform.processor()
        pyver = platform.python_version()
    except Exception:
        node = ""
        release = ""
        version = ""
        machine = ""
        processor = ""
        pyver = platform.python_version()

    info_lines = [
        f"OS:       {system}" + (f" ({pretty_name})" if pretty_name else ""),
        f"Release:  {release}",
        f"Version:  {version}",
        f"Machine:  {machine}",
        f"Python:   {pyver}",
    ]

    # color selection
    c = colors_mod
    Y = getattr(c, "YELLOW", "\033[33m")
    C = getattr(c, "LIGHT_GREEN", "\033[92m")
    B = getattr(c, "GREY", "\033[90m")
    BLU = getattr(c, "BLUE", "\033[94m")   # neu
    RST = getattr(c, "RESET", "\033[0m")

    color_for_key = {
        "windows": BLU,   # hier statt B
        "macos": C,
        "ubuntu": Y,
        "tux": C,
        "chromium": B,
    }
    prefix = color_for_key.get(key, "")
    suffix = RST if prefix else ""

    # pad lines to same height
    height = max(len(logo_lines), len(info_lines))
    logo_padded = logo_lines + [""] * (height - len(logo_lines))
    info_padded = info_lines + [""] * (height - len(info_lines))

    combined = []
    gap = "   "
    for l_logo, l_info in zip(logo_padded, info_padded):
        colored_logo = f"{prefix}{l_logo}{suffix}" if l_logo else ""
        combined.append(f"{colored_logo.ljust(14)}{gap}{l_info}")

    if node:
        combined.append(f"{'':14}{gap}Host:     {node}")

    return "\n".join(combined)

# --- new command implementations (insert near other command defs) ---


def date(args: dict) -> str:
    """Print the current date/time."""
    return datetime.datetime.now().isoformat(sep=" ")


def uname(args: dict) -> str:
    """Show system information (like uname -a)."""
    u = platform.uname()
    return " ".join([u.system, u.node, u.release, u.version, u.machine, u.processor or ""])


def hostname(args: dict) -> str:
    """Return hostname."""
    try:
        return platform.node() or os.uname().nodename
    except Exception:
        return os.environ.get("HOSTNAME", "") or os.environ.get("COMPUTERNAME", "")


def uptime(args: dict) -> str:
    """Approximate uptime (Linux via /proc/uptime, fallback N/A)."""
    try:
        if os.path.exists("/proc/uptime"):
            with open("/proc/uptime", "r") as f:
                secs = float(f.readline().split()[0])
            m, s = divmod(int(secs), 60)
            h, m = divmod(m, 60)
            d, h = divmod(h, 24)
            return f"{d}d {h}h {m}m {s}s"
        # Windows fallback: use system boot time via performance counters if available
        return "uptime not available"
    except Exception:
        return "uptime not available"


def whoami_cmd(args: dict) -> str:
    """Return current username."""
    try:
        return os.getlogin()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def sleep(args: dict) -> str:
    """Sleep N seconds (blocking)."""
    try:
        n = float(args.get("str", "1"))
    except Exception:
        n = 1.0
    time.sleep(max(0.0, n))
    return ""


def basename(args: dict) -> str:
    """Return basename of path."""
    return os.path.basename(args.get("str", "") or "")


def dirname(args: dict) -> str:
    """Return dirname of path."""
    return os.path.dirname(args.get("str", "") or "")


def which(args: dict) -> str:
    """Return full path of executable (shutil.which)."""
    name = args.get("str", "")
    if not name:
        return ""
    path = shutil.which(name)
    return path or ""


def true_cmd(args: dict) -> str:
    """Do nothing, succeed."""
    return ""


def false_cmd(args: dict) -> str:
    """Always 'fail' — but do not exit shell. Return non-empty string to indicate false."""
    return "false"


def yes(args: dict) -> str:
    """Repeat a string (default 'y') many times (limited)."""
    s = args.get("str", "y")
    count = int(args.get("n", 100))
    return "\n".join([s] * max(0, min(10000, count)))


def sort_cmd(args: dict) -> str:
    """Sort lines from input or a file."""
    txt = args.get("str", "")
    if args.get("file"):
        try:
            with open(args["file"], "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception as e:
            return f"sort failed: {e}"
    lines = txt.splitlines()
    return "\n".join(sorted(lines))


def uniq(args: dict) -> str:
    """uniq: remove consecutive duplicate lines by default; --all removes all duplicates."""
    txt = args.get("str", "")
    all_dup = str(args.get("all", "")).strip().lower() in ("1", "true", "yes")
    lines = txt.splitlines()
    if all_dup:
        seen = set()
        out = []
        for l in lines:
            if l not in seen:
                seen.add(l)
                out.append(l)
        return "\n".join(out)
    # consecutive unique
    out = []
    prev = None
    for l in lines:
        if l != prev:
            out.append(l)
        prev = l
    return "\n".join(out)


def head_cmd(args: dict) -> str:
    """Head: first N lines from text or file."""
    n = int(args.get("n", 10))
    txt = args.get("str", "")
    if args.get("file"):
        try:
            with open(args["file"], "r", encoding="utf-8", errors="replace") as f:
                return "\n".join([next(f).rstrip("\n") for _ in range(n) if not f.closed])
        except StopIteration:
            return ""
        except Exception as e:
            return f"head failed: {e}"
    lines = txt.splitlines()
    return "\n".join(lines[:n])


def tail_cmd(args: dict) -> str:
    """Tail: last N lines from text or file."""
    n = int(args.get("n", 10))
    txt = args.get("str", "")
    if args.get("file"):
        try:
            with open(args["file"], "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            return "\n".join(lines[-n:])
        except Exception as e:
            return f"tail failed: {e}"
    lines = txt.splitlines()
    return "\n".join(lines[-n:])


def wc_cmd(args: dict) -> str:
    """wc: count lines, words, bytes for given text or file."""
    txt = args.get("str", "")
    if args.get("file"):
        try:
            with open(args["file"], "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except Exception as e:
            return f"wc failed: {e}"
    lines = txt.count("\n")
    words = len(txt.split())
    bytes_len = len(txt.encode("utf-8"))
    return f"{lines} {words} {bytes_len}"


def clear(args: dict) -> str:
    """Clear terminal screen."""
    try:
        os.system("cls" if os.name == "nt" else "clear")
        return ""
    except Exception:
        return ""


def apt(args: dict) -> str:
    """
    Simple apt wrapper: `apt install <ModuleName>` installs a PowerShell module via pwsh.
    """
    raw = args.get("str", "") or ""
    parts = shlex.split(raw)
    if not parts or parts[0] != "install" or len(parts) < 2:
        return "usage: apt install <PowerShellModule>"
    pkg = parts[1]
    try:
        # Prefer pwsh if present
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            return "pwsh/powershell not found"
        cmd = [exe, "-NoProfile", "-Command",
               f"Install-Module -Name {pkg} -Force -Scope CurrentUser"]
        # force UTF-8 decoding and replace invalid bytes
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
        if proc.returncode == 0:
            return f"Installed {pkg}"
        return proc.stderr or proc.stdout or f"apt failed (code {proc.returncode})"
    except Exception as e:
        return f"apt failed: {e}"


def sudo(args: dict) -> str:
    """
    Run command elevated. On Windows attempt Start-Process -Verb RunAs (pwsh/powershell).
    On Unix use sudo.
    """
    cmdline = args.get("str", "")
    if not cmdline:
        return "usage: sudo <command>"
    try:
        if os.name == "nt":
            exe = shutil.which("powershell") or shutil.which("pwsh")
            if not exe:
                return "powershell/pwsh not found for elevation"
            # build PowerShell Start-Process invocation
            ps_cmd = f'Start-Process {exe} -ArgumentList \'-NoProfile -Command "{cmdline}"\' -Verb RunAs'
            # run without capturing output (elevation UI) but protect decoding if any output collected
            subprocess.run([shutil.which("powershell") or "powershell", "-NoProfile",
                           "-Command", ps_cmd], check=True, encoding="utf-8", errors="replace")
            return ""
        else:
            # Unix: run via sudo; capture output with utf-8 decode to avoid readerthread decode errors
            proc = subprocess.run(["sudo"] + shlex.split(cmdline),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                return proc.stdout.strip() or ""
            return proc.stderr or proc.stdout or f"sudo failed (code {proc.returncode})"
    except Exception as e:
        return f"sudo failed: {e}"


def lupdate(args: dict) -> str:
    # Pfad zur Konfigurationsdatei im User-Home
    config_path = os.path.join(os.path.expanduser("~"), ".luw-config-path")

    # Inhalt lesen (z.B. "C:/Users/Adam/LUW-Shell/app.py")
    with open(config_path, "r", encoding="utf-8") as f:
        app_path = f.read().strip()

    # Elternordner extrahieren -> Installationsverzeichnis
    INSTPATH = os.path.dirname(app_path)
    os.system(f'"{INSTPATH}/LUW-Shell-Update.exe"')
    return "Launched Update process. A new window should open."

# --- end new command implementations ---


COMMANDS = {
    "reverse": reverse,
    "upper": upper,
    "calc": calculator,
    "time": datetime_now,
    "echo": echo,
    "ls": ls,
    "pwd": pwd,
    "cd": cd,
    "whoami": whoami,
    "sysinfo": sysinfo,
    "disk": disk_usage,
    "rand": rand,
    "grep": grep,
    "cat": cat,
    "head": head,
    "tail": tail,
    "cp": cp,
    "mv": mv,
    "rm": rm,
    "mkdir": mkdir_cmd,
    "touch": touch,
    "stat": file_info,
    "info": file_info,
    "wc": wc,
    "json": json_pretty,
    "http_get": http_get,
    "http": http_get,
    "b64e": b64_encode,
    "b64d": b64_decode,
    "find": find,
    "cowsay": cowsay,
    "os-type": os_type,
    "lupdate": lupdate
}

COMMANDS.update({
    "date": date,
    "uname": uname,
    "hostname": hostname,
    "uptime": uptime,
    "whoami": whoami_cmd,
    "sleep": sleep,
    "basename": basename,
    "dirname": dirname,
    "which": which,
    "true": true_cmd,
    "false": false_cmd,
    "yes": yes,
    "sort": sort_cmd,
    "uniq": uniq,
    "head": head_cmd,
    "tail": tail_cmd,
    "wc": wc_cmd,
    "clear": clear,
    "apt": apt,
    "sudo": sudo,
    # ...add more as needed...
})


def _parse_dot_cow(content: str):
    """
    Parse a .cow file content and return (template, defaults) or (None, None) on failure.
    template: string between the EOC here-doc markers (the actual ASCII art template)
    defaults: dict with optional 'eyes' and 'tongue'
    """
    try:
        # Remove comment lines (start with # or ##)
        lines = content.splitlines()
        non_comment = [l for l in lines if not l.strip().startswith("#")]
        content_nc = "\n".join(non_comment)
        # find here-doc block assigned to $the_cow
        m = re.search(
            r"\$the_cow\s*=\s*<<['\"]?EOC['\"]?;\s*(.*?)\nEOC", content_nc, re.DOTALL | re.M)
        if not m:
            return None, {}
        template = m.group(1).rstrip("\n")
        defaults = {}
        # find default eyes/tongue patterns like: $eyes = $eyes || "o.o";
        m_eyes = re.search(
            r"\$eyes\s*=\s*\$eyes\s*\|\|\s*['\"]([^'\"]+)['\"]", content_nc)
        if m_eyes:
            defaults["eyes"] = m_eyes.group(1)
        m_tongue = re.search(
            r"\$tongue\s*=\s*\$tongue\s*\|\|\s*['\"]([^'\"]+)['\"]", content_nc)
        if m_tongue:
            defaults["tongue"] = m_tongue.group(1)
        return template, defaults
    except Exception:
        return None, {}


def _render_dot_cow(content: str, msg: str, eyes: str = None, tongue: str = None):
    """
    Render a .cow file content with provided msg, eyes and tongue.
    Returns a string or None on failure.
    """
    tpl, defaults = _parse_dot_cow(content)
    if not tpl:
        return None
    # determine values
    eyes_val = eyes or defaults.get("eyes") or "oo"
    tongue_val = tongue or defaults.get("tongue") or "  "
    # prepare thoughts placeholder: keep spacing similar to template usage
    thoughts_val = " "  # single space used where $thoughts appears in templates
    # substitute placeholders (simple literal replacement)
    try:
        out = tpl.replace("$thoughts", thoughts_val).replace(
            "$eyes", eyes_val).replace("$tongue", tongue_val)
        # Strip any leading/trailing empty lines from template output
        return "\n".join(line.rstrip() for line in out.splitlines()).rstrip("\n")
    except Exception:
        return None
