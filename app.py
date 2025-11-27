import argparse
import os
import json
import gzip
import time
import sys
import tempfile
import subprocess
from modules import initializer
from modules import dispatch
from modules import colors  # NEW: color helpers
from modules import commands as commands_module  # for completion list
from modules import logger  # NEW: centralized logger

_LE_MAGIC = b"LUWEXE1"  # 7 bytes header (magic + version)

def write_config_path():
    user_home = os.path.expanduser("~")
    config_file = os.path.join(user_home, ".luw-config-path")

    # Pfad zur app.py im aktuellen Arbeitsverzeichnis
    app_path = os.path.abspath("app.py")

    # Nur beim ersten Start schreiben
    if not os.path.exists(config_file):
        with open(config_file, "w") as f:
            f.write(app_path + "\n")
        print(f"Pfad gespeichert: {app_path}")
    else:
        pass

def _resolve_input_path(p: str) -> str:
    """
    Try to resolve input path p in multiple locations:
    - as given
    - relative to cwd
    - relative to this script's directory (app directory)
    Returns absolute path or raises FileNotFoundError.
    """
    if os.path.isabs(p) and os.path.isfile(p):
        return os.path.abspath(p)
    # try as given (relative to cwd)
    cand = os.path.abspath(p)
    if os.path.isfile(cand):
        return cand
    # try relative to current working directory explicitly (redundant but explicit)
    cand = os.path.join(os.getcwd(), p)
    if os.path.isfile(cand):
        return os.path.abspath(cand)
    # try relative to the app directory (where this file resides)
    app_dir = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(app_dir, p)
    if os.path.isfile(cand):
        return os.path.abspath(cand)
    # try adding common extensions if missing
    for ext in (".latin", ".py", ".txt"):
        cand = os.path.abspath(p + ext)
        if os.path.isfile(cand):
            return cand
        cand = os.path.join(os.getcwd(), p + ext)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
        cand = os.path.join(app_dir, p + ext)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    raise FileNotFoundError(p)

def compile_latin(script_path: str, out_path: str = None) -> str:
    """
    Produce a .le binary from a LUW script file.
    Format:
      - 7 byte magic: b"LUWEXE1"
      - 8 byte big-endian manifest length (unsigned)
      - manifest JSON (utf-8), contains {name, created, py_version, entry: "<original name>", type: "luw"|"python"}
      - gzip-compressed payload (utf-8 script text)
    Returns output path.
    """
    # Resolve script_path robustly so users can pass relative names like "app.py"
    script_abspath = _resolve_input_path(script_path)

    with open(script_abspath, "rb") as f:
        script_bytes = f.read()

    # detect type based on extension: .py -> python, else -> luw
    ext = os.path.splitext(script_abspath)[1].lower()
    script_type = "python" if ext == ".py" else "luw"

    manifest = {
        "name": os.path.splitext(os.path.basename(script_abspath))[0],
        "entry": os.path.basename(script_abspath),
        "created": time.time(),
        "py_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "type": script_type,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(script_bytes)
    if not out_path:
        out_path = os.path.splitext(script_abspath)[0] + ".le"
    out_abspath = os.path.abspath(out_path)
    with open(out_abspath, "wb") as out:
        out.write(_LE_MAGIC)
        # write 8-byte length
        out.write(len(manifest_bytes).to_bytes(8, "big"))
        out.write(manifest_bytes)
        out.write(compressed)
    return out_abspath

def load_and_run_le(le_path: str, master):
    """
    Load a .le file and execute its embedded script.
    - If manifest.type == "python": write payload to a temp .py and run it with the Python interpreter (streamed output).
    - If manifest.type == "luw": execute line-by-line via dispatch.handle_input(master, line).
    Returns True on success, raises on errors.
    """
    if not os.path.isfile(le_path):
        raise FileNotFoundError(le_path)
    with open(le_path, "rb") as f:
        magic = f.read(len(_LE_MAGIC))
        if magic != _LE_MAGIC:
            raise ValueError("Not a LUW binary (.le) or unsupported version")
        manifest_len = int.from_bytes(f.read(8), "big")
        manifest_bytes = f.read(manifest_len)
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Invalid manifest in {le_path}: {e}")
        payload = f.read()
        try:
            script_bytes = gzip.decompress(payload)
        except Exception as e:
            raise ValueError(f"Invalid payload (not gzip?) in {le_path}: {e}")
        script_text = script_bytes.decode("utf-8", errors="replace")

    # If this is a python binary, run it by writing to a temp .py and executing with the interpreter.
    if manifest.get("type") == "python":
        # Save the payload next to the .le so relative resources/imports resolve
        le_dir = os.path.dirname(os.path.abspath(le_path)) or "."
        entry_name = manifest.get("entry") or (manifest.get("name") + ".py")
        target_path = os.path.join(le_dir, entry_name)
        # avoid clobbering an existing file: append timestamp suffix if needed
        if os.path.exists(target_path):
            base, ext = os.path.splitext(entry_name)
            suffix = f"_le_run_{int(time.time())}"
            target_path = os.path.join(le_dir, f"{base}{suffix}{ext or '.py'}")
        try:
            with open(target_path, "wb") as tf:
                tf.write(script_bytes)
                tf.flush()
            # Execute from the .le directory so relative paths work
            rc = subprocess.run([sys.executable, target_path], check=False, text=True, encoding="utf-8", errors="replace", cwd=le_dir)
            return rc.returncode == 0
        except Exception as e:
            raise RuntimeError(f"Failed to write/run python payload: {e}")

    # Execute same as --script for LUW scripts: iterate lines and dispatch
    for raw in script_text.splitlines():
        line = raw.rstrip("\n")
        if not line or line.strip().startswith("#"):
            continue
        # don't echo lines automatically here (consistent with --script)
        dispatch.handle_input(master, line)
    try:
        master.wait_for_completion()
    except Exception:
        pass
    return True

if __name__ == "__main__":
    write_config_path()

# Parse args early so we can disable interactive subsystems in script mode
parser = argparse.ArgumentParser(description="LUW Shell Initializer")
parser.add_argument("--thread-count", type=int, default=4, help="Anzahl der Worker Threads")
parser.add_argument("--script", type=str, help="Run a LUW script file and exit")
parser.add_argument("--compile", type=str, help="Compile a LUW script to a .le binary (no external tools)")
parser.add_argument("--binary", type=str, help="Run a compiled LUW binary (.le)")
parsed_args, remaining_argv = parser.parse_known_args()
THREADCOUNT = parsed_args.thread_count
SCRIPT_PATH = parsed_args.script
COMPILE_PATH = parsed_args.compile
BINARY_PATH = parsed_args.binary

# Attempt to provide line-editing + history + completion:
HISTFILE = os.path.expanduser("~/.luw_history")

input_func = input  # default fallback
_have_readline = False
_have_prompt_toolkit = False

# --- NEW: helpers for command-specific flag completion ---
COMMON_FLAGS = {
	"cd": ["--mkdir", "--create", "--p"],
	"cowsay": ["--animal", "--colour", "--color", "-a"],
	"apt": ["install", "remove", "search", "--force"],
	"head": ["--n"],
	"tail": ["--n"],
	"grep": ["--pattern", "--file", "-n", "-i"],
	"find": ["--name", "--path"],
	"json": ["--file"],
	"wc": ["--file", "--bytes"],
	"set": [],
	"get": [],
	"ls": ["--all", "--long", "--path"],
	"echo": [],
	"reverse": [],
	"upper": [],
	"pwd": [],
	"sudo": [],
	"sleep": [],
    "!pwsh": [],
}

import re
import shlex
def get_flags_for_command(cmd_name: str):
	"""
	Return list of possible flags/params for cmd_name.
	- Prefer explicit COMMON_FLAGS
	- Fallback: parse command function docstring for tokens starting with --
	"""
	out = []
	if not cmd_name:
		return out
	if cmd_name in COMMON_FLAGS:
		out.extend(COMMON_FLAGS[cmd_name])
	# parse docstring for --flags
	try:
		func = getattr(commands_module, "COMMANDS", {}).get(cmd_name)
		if func and getattr(func, "__doc__", None):
			doc = func.__doc__ or ""
			found = re.findall(r'(--[A-Za-z0-9_\-:]+)', doc)
			for f in found:
				if f not in out:
					out.append(f)
	except Exception:
		pass
	# also include generic flags seen across commands
	generic = ["--file", "--n", "--path", "--pattern", "--recursive", "--help", "--str", "--url"]
	for g in generic:
		if g not in out:
			out.append(g)
	return out

# Only initialize line-editing/completion if not running a script
if not SCRIPT_PATH:
    # Try readline (Unix)
    try:
        import readline
        import atexit
        import glob
        from pathlib import Path

        _have_readline = True
        try:
            readline.read_history_file(HISTFILE)
        except Exception:
            pass
        atexit.register(lambda: readline.write_history_file(HISTFILE))
        readline.parse_and_bind("tab: complete")

        def _completer(text, state):
            """
            Readline completer that is aware of the full line buffer.
            - If first token is a known command and we're completing a subsequent token,
              suggest flags/params for that command.
            - Otherwise suggest command names or filesystem entries.
            """
            buf = readline.get_line_buffer()
            # split like shell, keeping trailing space awareness
            try:
                parts = shlex.split(buf)
                ended_with_space = buf.endswith(" ")
                if ended_with_space:
                    parts.append("")
            except Exception:
                parts = buf.split()
                ended_with_space = buf.endswith(" ")

            # If user already typed a shell-invocation token, suppress command completions
            if parts and parts[0] in ("!pwsh", "!cmd"):
                # no completions for commands after !pwsh/!cmd
                return None

            # determine current token being completed
            current = ""
            if parts:
                current = parts[-1]
            else:
                current = text

            candidates = []
            # if we have a command as first token and we are completing later tokens, offer flags
            if parts and parts[0] in getattr(commands_module, "COMMANDS", {}):
                cmd = parts[0]
                # if completing first token (no space yet) -> suggest commands
                if len(parts) == 1 and not ended_with_space:
                    # suggest commands matching current
                    candidates = [c for c in sorted(commands_module.COMMANDS.keys()) if c.startswith(current)]
                else:
                    # suggest flags for this command
                    flags = get_flags_for_command(cmd)
                    candidates = [f for f in flags if f.startswith(current)]
                    # if token looks like a path, fallback to filesystem
                    if not candidates and (current.startswith((".", "/", "\\")) or (len(current) >= 2 and current[1] == ":")):
                        candidates = glob.glob(current + "*")
            else:
                # no command yet: suggest commands + specials
                cmd_keys = list(getattr(commands_module, "COMMANDS", {}).keys())
                specials = ["!pwsh", "!cmd", "!multithread", "!mt", "exit", "quit", "cd", "pwd", "ls", "env", "set", "get", "alias", "unalias", "aliases", "help"]
                candidates = sorted({c for c in (cmd_keys + specials) if c.startswith(text)})
                if not candidates:
                    candidates = glob.glob(text + "*")

            # dedupe and return state-th candidate
            candidates = list(dict.fromkeys(candidates))
            try:
                return candidates[state]
            except IndexError:
                return None

        readline.set_completer(_completer)

    except Exception:
        # Try prompt_toolkit (cross-platform, optional)
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.completion import Completer, Completion, PathCompleter
            from prompt_toolkit.lexers import Lexer
            from prompt_toolkit.styles import Style
            import atexit
            import shlex
            import glob

            _have_prompt_toolkit = True
            words = list(getattr(commands_module, "COMMANDS", {}).keys())
            specials = ["!pwsh", "!cmd", "!multithread", "!mt", "exit", "quit", "cd", "pwd", "ls", "env", "set", "get", "alias", "unalias", "aliases", "help"]
            # dedupe once here
            all_candidates = sorted(set(words + specials))
            path_completer = PathCompleter(expanduser=True)

            # --- NEW: inline syntax highlighter lexer ---
            class ShellLexer(Lexer):
                """
                Simple lexer that splits the current input line with shlex and returns
                styled fragments for command/flags/paths/numbers/args.
                """
                def lex_document(self, document):
                    def get_line(lineno):
                        line = document.lines[lineno]
                        fragments = []
                        if not line:
                            return [('', '')]
                        try:
                            parts = shlex.split(line)
                        except Exception:
                            parts = line.split()
                        pos = 0
                        for i, part in enumerate(parts):
                            # find the next occurrence of part starting from pos
                            idx = line.find(part, pos)
                            if idx == -1:
                                # fallback: append rest as plain
                                break
                            # append intermediate whitespace
                            if idx > pos:
                                fragments.append(('', line[pos:idx]))
                            # choose style
                            if i == 0:
                                sty = 'class:command'
                            elif part.startswith('--') or (part.startswith('-') and len(part) > 1):
                                sty = 'class:flag'
                            elif re.match(r'^\d+$', part):
                                sty = 'class:number'
                            elif part.startswith((".", "/", "\\")) or (len(part) >= 2 and part[1] == ':'):
                                sty = 'class:path'
                            else:
                                sty = 'class:arg'
                            fragments.append((sty, part))
                            pos = idx + len(part)
                        # trailing text (whitespace or unmatched)
                        if pos < len(line):
                            fragments.append(('', line[pos:]))
                        if not fragments:
                            return [('', line)]
                        return fragments
                    return get_line

            # --- style definitions ---
            shell_style = Style.from_dict({
                'command':        'bold ansiblue',
                'flag':           'ansiyellow',
                'path':           'ansigreen',
                'number':         'ansimagenta',
                'arg':            'ansicyan',
                # fallback / punctuation
                '':               '',
            })

            class PTKCompleter(Completer):
                """
                Token-aware completer: splits the buffer with shlex and completes the current token.
                Offers command-specific flag completions when appropriate, and filesystem completions when token looks like a path.
                """
                def __init__(self, candidates, specials):
                    self.candidates = candidates
                    self.specials = specials

                def get_completions(self, document, complete_event):
                    text = document.text_before_cursor
                    try:
                        parts = shlex.split(text)
                        ended_with_space = text.endswith(" ")
                        if ended_with_space:
                            parts.append("")
                    except Exception:
                        parts = text.split()
                        ended_with_space = text.endswith(" ")
                    current = parts[-1] if parts else ""

                    # If user started a raw shell invocation, do not offer command completions
                    if parts and parts[0] in ("!pwsh", "!cmd"):
                        return

                    # if current token looks like a path -> provide filesystem completions
                    if current.startswith((".", "/", "\\")) or (len(current) >= 2 and current[1] == ":"):
                        for p in glob.glob(current + "*"):
                            yield Completion(p, start_position=-len(current))
                        return

                    # if first token is a known command, and we are past the first token, suggest flags
                    if parts and parts[0] in getattr(commands_module, "COMMANDS", {}):
                        cmd = parts[0]
                        # if we're still completing the command name itself
                        if len(parts) == 1 and not ended_with_space:
                            for cand in self.candidates:
                                if cand.startswith(current):
                                    yield Completion(cand, start_position=-len(current))
                            return
                        # otherwise suggest flags for that command
                        flags = get_flags_for_command(cmd)
                        for f in flags:
                            if f.startswith(current):
                                yield Completion(f, start_position=-len(current))
                        # also fallback to path completions if token appears path-like
                        return

                    # default: suggest command names
                    for cand in self.candidates:
                        if cand.startswith(current):
                            yield Completion(cand, start_position=-len(current))

            completer = PTKCompleter(all_candidates, specials)
            # Use ShellLexer and shell_style in PromptSession to enable live highlighting
            session = PromptSession(history=FileHistory(HISTFILE), completer=completer,
                                    lexer=ShellLexer(), style=shell_style)

            def _prompt_toolkit_input(prompt):
                try:
                    return session.prompt(prompt)
                except KeyboardInterrupt:
                    raise

            input_func = _prompt_toolkit_input

        except Exception:
            # Minimal fallback: keep a simple history file (no real-time completion)
            import atexit
            try:
                _hist_lines = []
                if os.path.exists(HISTFILE):
                    with open(HISTFILE, "r", encoding="utf-8", errors="ignore") as f:
                        _hist_lines = [line.rstrip("\n") for line in f.readlines()]
            except Exception:
                _hist_lines = []

            def _save_history():
                try:
                    with open(HISTFILE, "w", encoding="utf-8") as f:
                        f.write("\n".join(_hist_lines[-1000:]) + ("\n" if _hist_lines else ""))
                except Exception:
                    pass

            atexit.register(_save_history)

            def _minimal_input(prompt):
                try:
                    line = input(prompt)
                except KeyboardInterrupt:
                    raise
                if line:
                    _hist_lines.append(line)
                return line

            input_func = _minimal_input
else:
    # Script mode: disable interactive completers and history
    logger.log(f"Running script: {SCRIPT_PATH}")
    # keep input_func as default; we'll not prompt in script mode

logger.log("LUW Shell")
logger.log("NOT AN OFFICIAL MICROSOFT PRODUCT")

# Use parsed THREADCOUNT; initialize master
Initializer = initializer.Initializer(THREADCOUNT)
master = Initializer.init()

# If script provided, execute lines and exit
if SCRIPT_PATH:
    try:
        with open(SCRIPT_PATH, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line or line.strip().startswith("#"):
                    continue
                # show the command being executed only when debug is enabled
                if not logger.is_debug_suppressed():
                    try:
                        print(line)
                    except Exception:
                        pass
                # execute via dispatch (same behavior as interactive)
                dispatch.handle_input(master, line)
        # Wait for queued tasks to finish
        try:
            master.wait_for_completion()
        except Exception:
            pass
    except FileNotFoundError:
        logger.log(f"Script not found: {SCRIPT_PATH}")
    except Exception as e:
        logger.log(f"Error running script: {e}")
    # exit after script
    import sys as _sys
    _sys.exit(0)

# If compile requested, produce .le file and exit (does not change --script behavior)
if COMPILE_PATH:
    try:
        out = compile_latin(COMPILE_PATH, None)
        logger.log(f"Compiled {COMPILE_PATH} -> {out}")
        print(out)
    except Exception as e:
        logger.log(f"Compilation failed: {e}")
        print(f"Compilation failed: {e}")
    import sys as _sys
    _sys.exit(0)

# If binary execution requested explicitly via --binary, run it and exit
if BINARY_PATH:
    Initializer = initializer.Initializer(THREADCOUNT)
    master = Initializer.init()
    try:
        load_and_run_le(BINARY_PATH, master)
    except Exception as e:
        logger.log(f"Failed to run binary {BINARY_PATH}: {e}")
        print(f"Failed to run binary {BINARY_PATH}: {e}")
    import sys as _sys
    _sys.exit(0)

# If --script provided and file endswith .le treat as binary as well
if SCRIPT_PATH and SCRIPT_PATH.lower().endswith(".le"):
    Initializer = initializer.Initializer(THREADCOUNT)
    master = Initializer.init()
    try:
        load_and_run_le(SCRIPT_PATH, master)
    except Exception as e:
        logger.log(f"Failed to run binary script {SCRIPT_PATH}: {e}")
        print(f"Failed to run binary script {SCRIPT_PATH}: {e}")
    import sys as _sys
    _sys.exit(0)

# ...existing interactive loop follows unchanged...
while True:
    # recompute working path before each prompt so 'cd' is reflected immediately
    WORKINGPATH = os.getcwd()
    try:
        command_line = input_func(f"LUW {WORKINGPATH}: ")
    except KeyboardInterrupt:
        print()  # newline, remain in shell
        continue
    if command_line is None:
        continue
    if command_line.lower() in ("exit", "quit"):
        # show colored exit token as feedback
        break

    # Echo colored input so special commands and flags are visible


    # delegate parsing/execution to dispatch module
    dispatch.handle_input(master, command_line)
