# ANSI color helpers and simple token-aware highlighter

RESET = "\033[0m"
YELLOW = "\033[33m"
GREY = "\033[90m"
LIGHT_GREEN = "\033[92m"
DIM = "\033[2m"

SPECIAL_CMD_SET = {"!pwsh", "!cmd", "!multithread", "!mt"}
EXIT_SET = {"exit", "quit"}

def color_token(token: str) -> str:
    """Color a single token according to simple rules."""
    if token in SPECIAL_CMD_SET:
        return f"{YELLOW}{token}{RESET}"
    if token in EXIT_SET:
        return f"{LIGHT_GREEN}{token}{RESET}"
    if token.startswith("--"):
        return f"{GREY}{token}{RESET}"
    return token

def color_command_line(cmd_line: str) -> str:
    """Return a colorized representation of the command line (for echoing)."""
    import shlex
    try:
        parts = shlex.split(cmd_line)
    except Exception:
        # fallback: naive split
        parts = cmd_line.split()
    if not parts:
        return ""
    colored_parts = [color_token(parts[0])]
    # color remaining tokens: flags grey, special cmds yellow, exit green
    for t in parts[1:]:
        colored_parts.append(color_token(t))
    return " ".join(colored_parts)

# convenience shortnames
highlight = color_token
colorize = color_command_line
