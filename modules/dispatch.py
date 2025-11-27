import shlex
import queue
import os
import re
from . import shell_executor
from . import commands as commands_module
from . import colors
from . import logger  # NEW: centralized logger

_env_pattern = re.compile(r'\$env:([A-Za-z_]\w*)|\${([A-Za-z_]\w*)}|\$([A-Za-z_]\w*)')


def _expand_env_in_str(s: str) -> str:
    if not s:
        return s

    def repl(m):
        name = m.group(1) or m.group(2) or m.group(3)
        return os.environ.get(name, "")

    return _env_pattern.sub(repl, s)


def _expand_env_in_args(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            out[k] = _expand_env_in_str(v)
        else:
            out[k] = v
    return out


def _was_quoted(token: str, original_text: str) -> bool:
    if not original_text:
        return False
    if f'"{token}"' in original_text or f"'{token}'" in original_text:
        return True
    if f"${{{token}}}" in original_text:
        return True
    return False


def _build_args_from_parts(parts, original_text: str = None):
    """
    Build args dict; parts is a list (parts[0] is command).
    If original_text provided, positional tokens that exactly match command names
    may be expanded elsewhere (handled by composition detection). This function
    simply collects flags (--foo value) and the remaining positional tokens into args['str'].
    """
    args = {}
    i = 1
    positional = []
    while i < len(parts):
        if parts[i].startswith("--") and i + 1 < len(parts):
            args[parts[i].lstrip("-")] = parts[i + 1]
            i += 2
        else:
            positional.append(parts[i])
            i += 1
    if positional and "str" not in args:
        args["str"] = " ".join(positional)
    return args


def _extract_positional(parts):
    pos = []
    i = 1
    while i < len(parts):
        if parts[i].startswith("--") and i + 1 < len(parts):
            i += 2
        else:
            pos.append(parts[i])
            i += 1
    return pos


def _consumed_length(parts):
    """
    Given parts where parts[0] is a command, return how many tokens (including the command)
    are consumed by that command if parsed with _build_args_from_parts (flags take two tokens).
    """
    i = 1
    while i < len(parts):
        if parts[i].startswith("--") and i + 1 < len(parts):
            i += 2
        else:
            i += 1
    return i  # number of tokens consumed (command + its args)


# Recognized dispatch builtins (must match keys handled in _run_builtin)
BUILTIN_NAMES = {"cd", "pwd", "echo", "print", "ls", "env", "set", "get", "help", "alias", "unalias", "aliases"}

def _evaluate_composed_chain_if_any(command, parts, original_text: str = None):
    """
    Detect patterns like: outer [--flags] ... inner1 inner2 ... ARGS
    This variant searches for a contiguous chain of known commands anywhere
    among the positional tokens (not only at the start). If found it evaluates
    the innermost and applies the chain right-to-left, then substitutes the
    evaluated value back into the outer invocation while preserving flags.
    """
    positional = _extract_positional(parts)
    if not positional:
        return False, None

    # map positional tokens to their indices in parts (skip flags + their values)
    pos_indices = []
    i = 1
    while i < len(parts):
        if parts[i].startswith("--") and i + 1 < len(parts):
            i += 2
        else:
            pos_indices.append(i)
            i += 1

    if not pos_indices:
        return False, None

    # scan for a contiguous chain of known commands anywhere in positional
    found = False
    found_pos_idx = None
    chain = []
    for p_idx in range(len(positional)):
        # start candidate chain at this positional index
        if (positional[p_idx] in commands_module.COMMANDS or positional[p_idx] in BUILTIN_NAMES) and not _was_quoted(positional[p_idx], original_text):
            # build chain from here
            tmp_chain = []
            k = p_idx
            while k < len(positional) and (positional[k] in commands_module.COMMANDS or positional[k] in BUILTIN_NAMES) and not _was_quoted(positional[k], original_text):
                tmp_chain.append(positional[k])
                k += 1
            if tmp_chain:
                found = True
                found_pos_idx = p_idx
                chain = tmp_chain
                break

    if not found or not chain:
        return False, None

    # innermost command token and its start index in parts
    innermost = chain[-1]
    start_idx = None
    # find the first occurrence of the innermost token in parts starting from pos_indices[found_pos_idx]
    for idx in range(pos_indices[found_pos_idx], len(parts)):
        if parts[idx] == innermost:
            start_idx = idx
            break

    # fallback: construct parts_for_inner from positional remainder if we couldn't find exact start in parts
    if start_idx is None:
        parts_for_inner = [innermost] + positional[found_pos_idx + len(chain):]
    else:
        parts_for_inner = parts[start_idx:]

    # Evaluate innermost synchronously (handle builtins)
    try:
        inn_cmd = parts_for_inner[0]
        inn_args = _build_args_from_parts(parts_for_inner, original_text)
        if inn_cmd in BUILTIN_NAMES:
            handled, out = _run_builtin(inn_cmd, inn_args)
            if not handled:
                return False, None
            value = out
        else:
            func = commands_module.COMMANDS.get(inn_cmd)
            if not func:
                return False, None
            value = func(inn_args)
    except Exception:
        return False, None

    # Apply remaining chain (except innermost) right-to-left, handling builtins as well
    for cmd_name in reversed(chain[:-1]):
        try:
            if cmd_name in BUILTIN_NAMES:
                handled, out = _run_builtin(cmd_name, {"str": str(value)})
                if not handled:
                    return False, None
                value = out
            else:
                func = commands_module.COMMANDS.get(cmd_name)
                if not func:
                    return False, None
                value = func({"str": str(value)})
        except Exception:
            return False, None

    # Determine absolute indices in parts for the chain span:
    # start_pos is the absolute index of the first chain token in parts
    start_pos = pos_indices[found_pos_idx]

    # compute how many tokens the innermost consumed (relative to parts_for_inner)
    consumed = _consumed_length(parts_for_inner)  # includes the innermost command itself
    # find end_idx: absolute index of last token belonging to innermost + its args
    end_idx = start_idx + consumed - 1 if start_idx is not None else start_pos + consumed - 1

    # ensure end_pos covers at least the last chain token's position
    last_chain_pos_in_parts = pos_indices[found_pos_idx + len(chain) - 1] if (found_pos_idx + len(chain) - 1) < len(pos_indices) else start_pos
    end_pos = max(end_idx, last_chain_pos_in_parts)

    # Build new parts: everything before start_pos, then a single token with the computed value, then everything after end_pos
    new_parts = parts[:start_pos] + [str(value)] + parts[end_pos + 1 :]

    # Build args for outer from new_parts and call outer function (support builtin)
    try:
        outer_args = _build_args_from_parts(new_parts, original_text=" ".join(new_parts))
        if command in BUILTIN_NAMES:
            handled, out = _run_builtin(command, outer_args)
            if not handled:
                return False, None
            return True, str(out)
        outer_func = commands_module.COMMANDS.get(command)
        if not outer_func:
            return False, None
        final = outer_func(outer_args)
        return True, str(final)
    except Exception:
        return False, None


# helper to emit debug-only messages (respected by script !SuppressDebug)
def _dbg(msg: str):
    if not logger.is_debug_suppressed():
        logger.log(msg)

def _print_proc_output(stdout, stderr, err, label="sync"):
    """
    Print subprocess (or sync) outputs with a worker-style prefix:
    e.g. [Worker shell] <line>
    If debug is suppressed, print plain lines (no prefix).
    """
    prefix = f"[Worker {label}]"
    if logger.is_debug_suppressed():
        if err:
            for line in str(err).splitlines():
                print(line)
        if stdout:
            for line in str(stdout).splitlines():
                print(line)
        if stderr:
            for line in str(stderr).splitlines():
                print(line)
        return

    if err:
        for line in str(err).splitlines():
            logger.log(f"{prefix} {line}")
    if stdout:
        for line in str(stdout).splitlines():
            logger.log(f"{prefix} {line}")
    if stderr:
        for line in str(stderr).splitlines():
            logger.log(f"{prefix} {line}")

def _print_with_label(text, label="sync", result_tag=False):
    """
    Print text with a worker-style prefix. If result_tag True, prints "[Worker label] Ergebnis:" then the text.
    If debug is suppressed, print plain text (no prefix).
    """
    prefix = f"[Worker {label}]"
    if text is None:
        return
    s = str(text)
    if logger.is_debug_suppressed():
        print(s)
        return
    if result_tag:
        logger.log(f"{prefix} Ergebnis:")
        logger.log(s)
        return
    for line in s.splitlines():
        logger.log(f"{prefix} {line}")


# Simple in-memory aliases (session only)
ALIASES = {}

def _expand_aliases_line(line: str, max_depth: int = 5) -> str:
    """
    Expand an alias if first token matches one. Prevent infinite recursion by limiting depth.
    """
    if not line or not line.strip():
        return line
    new_line = line
    depth = 0
    while depth < max_depth:
        try:
            parts = shlex.split(new_line)
        except Exception:
            parts = new_line.split()
        if not parts:
            break
        first = parts[0]
        if first in ALIASES:
            expansion = ALIASES[first]
            remainder = parts[1:]
            if remainder:
                new_line = expansion + " " + " ".join(remainder)
            else:
                new_line = expansion
            depth += 1
            continue
        break
    return new_line


def _run_builtin(command, args):
    """
    Handle local builtins. Return (handled: bool, output: str).
    Supported: cd, pwd, echo, print, ls, env, set, get, help, alias, unalias, aliases
    """
    # docs for dispatch builtins (used by help)
    BUILTIN_DOCS = {
        "help": "help [command] - Show available commands or details for a command.",
        "alias": "alias name='expansion' - Define a session alias.",
        "unalias": "unalias name - Remove an alias.",
        "aliases": "aliases - List currently defined aliases.",
        "cd": "cd <path> - Change directory (supports --mkdir/--create/--p).",
        "pwd": "pwd - Print current directory.",
        "echo": "echo <text> - Print text (expands $env:VAR).",
        "print": "print <text> - Same as echo.",
        "ls": "ls [path] - List directory.",
        "env": "env - List environment variables.",
        "set": "set VAR=VALUE - Set environment variable.",
        "get": "get VAR - Get environment variable.",
        "lupdate": "lupdate - Update the shell"
    }

    # help: no args -> list commands and builtins; help <cmd> -> show docstring or builtin doc
    if command == "help":
        topic = args.get("str", "").strip()
        if not topic:
            # list commands + builtins
            lines = []
            # commands from modules.commands
            for name in sorted(commands_module.COMMANDS.keys()):
                func = commands_module.COMMANDS.get(name)
                summary = ""
                if func and func.__doc__:
                    summary = func.__doc__.strip().splitlines()[0]
                lines.append(f"{name:15} - {summary}")
            # builtins
            lines.append("\nBuiltins:")
            for bname, bdoc in sorted(BUILTIN_DOCS.items()):
                lines.append(f"{bname:15} - {bdoc}")
            return True, "\n".join(lines)
        else:
            # try module command first
            func = commands_module.COMMANDS.get(topic)
            if func:
                doc = func.__doc__ or ""
                return True, f"{topic}\n{doc}"
            # then builtins
            if topic in BUILTIN_DOCS:
                return True, f"{topic}\n{BUILTIN_DOCS[topic]}"
            return True, f"No such command: {topic}"

    # alias: alias name='cmd ...'
    if command == "alias":
        raw = args.get("str", "") or ""
        if not raw:
            return True, "usage: alias name='expansion'"
        try:
            if "=" in raw:
                name, rest = raw.split("=", 1)
                name = name.strip()
                rest = rest.strip().strip("'\"")
            else:
                parts = shlex.split(raw)
                name = parts[0]
                rest = " ".join(parts[1:]) if len(parts) > 1 else ""
            if not name or not rest:
                return True, "invalid alias"
            ALIASES[name] = rest
            return True, ""
        except Exception as e:
            return True, f"alias failed: {e}"

    if command == "unalias":
        name = args.get("str", "") or ""
        name = name.strip()
        if not name:
            return True, "usage: unalias name"
        ALIASES.pop(name, None)
        return True, ""

    if command == "aliases":
        if not ALIASES:
            return True, "no aliases"
        lines = [f"{k} -> {v}" for k, v in ALIASES.items()]
        return True, "\n".join(lines)

    # cd
    if command == "cd":
        path = args.get("str", "") or args.get("path", "")
        if not path:
            path = os.path.expanduser("~")
        try:
            os.chdir(_expand_env_in_str(path))
            # return current working dir so the shell can display it immediately
            return True, os.getcwd()
        except Exception as e:
            return True, f"cd failed: {e}"

    # pwd
    if command == "pwd":
        try:
            return True, os.getcwd()
        except Exception as e:
            return True, f"pwd failed: {e}"

    # echo / print
    if command in ("echo", "print"):
        text = args.get("str", "")
        return True, _expand_env_in_str(text)

    # ls
    if command == "ls":
        path = args.get("str", "") or args.get("path", ".")
        try:
            path = _expand_env_in_str(path)
            entries = sorted(os.listdir(path))
            return True, "\n".join(entries)
        except Exception as e:
            return True, f"ls failed: {e}"

    # env
    if command == "env":
        items = [f"{k}={v}" for k, v in os.environ.items()]
        return True, "\n".join(items)

    # set
    if command == "set":
        raw = args.get("str", "") or ""
        raw = raw.strip()
        if not raw:
            return True, "usage: set VAR=VALUE"
        if "=" in raw:
            left, right = raw.split("=", 1)
            name = left.strip()
            value = right.strip()
        else:
            try:
                parts = shlex.split(raw)
            except Exception:
                parts = raw.split()
            if len(parts) < 2:
                return True, "usage: set VAR=VALUE"
            name = parts[0]
            value = " ".join(parts[1:])
        if name.startswith("$env:"):
            name = name[len("$env:"):]

        if name.startswith("$"):
            name = name[1:]

        if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in ('"', "'")):
            value = value[1:-1]
        try:
            os.environ[name] = value
            return True, ""
        except Exception as e:
            return True, f"set failed: {e}"

    # get
    if command == "get":
        k = args.get("str", "")
        if not k:
            return True, ""
        if k.startswith("$env:"):
            k = k[len("$env:"):]

        if k.startswith("$"):
            k = k[1:]
        return True, os.environ.get(k, "")

    return False, None


def _evaluate_inner_chain_if_any(parts, original_text: str = None):
    """
    Detect an inner chain starting at the first positional token (parts[1]...), e.g.
    parts = ['reverse','upper','Hello'] -> detects chain ['upper'] and returns (True, inner_value)
    inner_value is the fully evaluated result of the chain (does NOT apply the outer command).
    Returns (False, None) if no inner chain found or on error.
    """
    positional = _extract_positional(parts)
    if not positional:
        return False, None

    chain = []
    idx = 0
    while idx < len(positional) and (positional[idx] in commands_module.COMMANDS or positional[idx] in BUILTIN_NAMES) and not _was_quoted(positional[idx], original_text):
        chain.append(positional[idx])
        idx += 1

    if not chain:
        return False, None

    innermost = chain[-1]
    start_idx = None
    for i in range(1, len(parts)):
        if parts[i] == innermost:
            start_idx = i
            break
    if start_idx is None:
        parts_for_inner = [innermost] + positional[idx:]
    else:
        parts_for_inner = parts[start_idx:]

    # Evaluate innermost (handle builtins)
    try:
        inn_cmd = parts_for_inner[0]
        inn_args = _build_args_from_parts(parts_for_inner, original_text)
        if inn_cmd in BUILTIN_NAMES:
            handled, out = _run_builtin(inn_cmd, inn_args)
            if not handled:
                return False, None
            value = out
        else:
            func = commands_module.COMMANDS.get(inn_cmd)
            if not func:
                return False, None
            value = func(inn_args)
    except Exception:
        return False, None

    # Apply remaining chain upwards (right-to-left excluding innermost)
    for cmd_name in reversed(chain[:-1]):
        try:
            if cmd_name in BUILTIN_NAMES:
                handled, out = _run_builtin(cmd_name, {"str": str(value)})
                if not handled:
                    return False, None
                value = out
            else:
                func = commands_module.COMMANDS.get(cmd_name)
                if not func:
                    return False, None
                value = func({"str": str(value)})
        except Exception:
            return False, None

    return True, str(value)


def handle_input(master, command_line: str):
    """
    Parse a single input line and either execute local shell commands (!pwsh / !cmd),
    builtins, composed synchronous chains, or enqueue tasks for worker threads.
    Supports !multithread / !mt with '&' separated subcommands.
    """
    if not command_line or not command_line.strip():
        return

    # Expand aliases before any parsing so aliases execute locally
    expanded_line = _expand_aliases_line(command_line)
    if expanded_line != command_line:
        # notify user about expansion (debug-only)
        _dbg(f"Alias expanded: {command_line} -> {expanded_line}")
        command_line = expanded_line

    # Script-specific debug suppression toggles
    cl = command_line.strip().lower()
    if cl in ("!suppressdebug", "!suppress-debug"):
        print("Debug suppressed for this session.")
        logger.set_debug_suppressed(True)
        return
    if cl in ("!resumedebug", "!enabledebug", "!unsuppressdebug"):
        logger.set_debug_suppressed(False)
        print("Debug resumed for this session.")
        return

    # cache frequently used objects to reduce attribute lookups
    commands_map = commands_module.COMMANDS
    # allow explicit debug logger and a logger used for results (logger.log always prints)
    log = logger.log
    # debug-only logger
    _dbg_fn = _dbg
    colorize = colors.colorize

    try:
        parts_all = shlex.split(command_line)
    except Exception:
        parts_all = command_line.split()
    if not parts_all:
        return

    command = parts_all[0]

    # top-level shell invocations
    if command == "!pwsh":
        # run and stream live
        raw = command_line[len(command):].strip()
        if not raw:
            _dbg("Keine pwsh Anweisung angegeben.")
            return
        rc, err = shell_executor.run_pwsh_stream(raw)
        if err:
            _print_with_label(f"pwsh failed: {err}", label="shell")
        else:
            # only show return code when debug is enabled
            _dbg(f"pwsh exited: {rc}")
        return

    if command == "!cmd":
        cmd_str = command_line[len(command):].strip()
        if not cmd_str:
            _print_with_label("Keine cmd-Anweisung angegeben.", label="shell")
            return
        stdout, stderr, err = shell_executor.run_cmd(cmd_str)
        _print_proc_output(stdout, stderr, err, label="shell")
        return

    # multithread mode
    if command in ("!multithread", "!mt"):
        remainder = command_line[len(command):].strip()
        if not remainder:
            _dbg("Keine Unterbefehle angegeben.")
            return

        try:
            tokens = shlex.split(remainder)
        except Exception:
            tokens = remainder.split()
        global_shell = None
        remainder_after_shell = remainder
        if tokens and tokens[0] in ("!pwsh", "!cmd"):
            global_shell = tokens[0]
            remainder_after_shell = remainder[len(tokens[0]):].strip()

        subcmds = [s.strip() for s in remainder_after_shell.split("&") if s.strip()]
        if not subcmds:
            _dbg("Keine Unterbefehle angegeben.")
            return

        task_count = 0
        for sub in subcmds:
            try:
                sub_parts = shlex.split(sub)
            except Exception:
                sub_parts = sub.split()
            if not sub_parts:
                continue

            shell_mode = global_shell
            cmd_parts_start = 0
            if not shell_mode and sub_parts and sub_parts[0] in ("!pwsh", "!cmd"):
                shell_mode = sub_parts[0]
                cmd_parts_start = 1

            if shell_mode:
                # stream the shell command output live instead of capturing then printing
                cmd_str = " ".join(sub_parts[cmd_parts_start:]).strip()
                if not cmd_str:
                    _print_with_label(f"Keine {shell_mode} Anweisung angegeben.", label="shell")
                    continue
                if shell_mode == "!pwsh":
                    rc, err = shell_executor.run_pwsh_stream(cmd_str)
                else:
                    rc, err = shell_executor.run_cmd_stream(cmd_str)
                if err:
                    _print_with_label(f"shell failed: {err}", label="shell")
                else:
                    _dbg(f"{shell_mode} exited: {rc}")
                continue

            # NEW: handle composed inner chains inside a multithread sub
            composed_inner_ok, inner_value = _evaluate_inner_chain_if_any(sub_parts, original_text=sub)
            if composed_inner_ok:
                outer_cmd = sub_parts[0]
                args_to_enqueue = {"str": inner_value}
                try:
                    _dbg(f"Enqueue: {colorize(outer_cmd + ' ' + inner_value)}")
                except Exception:
                    _dbg(f"Enqueue: {outer_cmd} {inner_value}")
                master.add_task(outer_cmd, args_to_enqueue)
                task_count += 1
                continue

            args = _build_args_from_parts(sub_parts, original_text=sub)
            handled, out = _run_builtin(sub_parts[0], args)
            if handled:
                if out:
                    _print_with_label(out, label="sync")
                continue

            args_expanded = _expand_env_in_args(args)
            try:
                _dbg(f"Enqueue: {colors.colorize(sub)}")
            except Exception:
                _dbg(f"Enqueue: {sub}")
            master.add_task(sub_parts[0], args_expanded)
            task_count += 1

        _dbg(f"Added {task_count} tasks (multithread)")
        for _ in range(task_count):
            try:
                worker_id, result = master.result_queue.get(timeout=10)
                _print_with_label(result, label=str(worker_id), result_tag=True)
            except queue.Empty:
                _dbg("Keine Antwort vom Worker erhalten (Timeout).")
        return

    # Attempt composed synchronous evaluation like: reverse upper Hello
    composed_ok, composed_result = _evaluate_composed_chain_if_any(command, parts_all, original_text=command_line)
    if composed_ok:
        _print_with_label(composed_result, label="sync", result_tag=True)
        return

    # single-command path: builtins or enqueue
    args = _build_args_from_parts(parts_all, original_text=command_line)
    handled, out = _run_builtin(command, args)
    if handled:
        if out:
            _print_with_label(out, label="sync")
        return

    # enqueue worker task
    args_expanded = _expand_env_in_args(args)
    try:
        _dbg_fn(f"Enqueue: {colorize(command_line)}")
    except Exception:
        _dbg_fn(f"Enqueue: {command_line}")
    master.add_task(command, args_expanded)
    try:
        # get result
        rq = master.result_queue
        if hasattr(rq, "get"):
            try:
                worker_id, result = rq.get()
            except TypeError:
                worker_id, result = rq.get(timeout=5)
        else:
            worker_id, result = rq.get(timeout=5)
        # print result without prefix when debug suppressed, otherwise with prefix
        if logger.is_debug_suppressed():
            # plain result line
            print(result)
        else:
            logger.log(f"[Worker {worker_id}] Ergebnis:")
            logger.log(result)
    except Exception as e:
        # show error always
        logger.log(f"Keine Antwort vom Worker erhalten (Timeout). Fehler: {e}")
