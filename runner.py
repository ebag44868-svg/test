# 워커 안에서 Pyodide가 실행하는 채점·실행 도우미
import sys, io, ast, time, traceback, linecache, json, builtins, types

MAX_OUT = 200_000      # 출력 글자 수 상한 (무한 print 방지)
MAX_STEPS = 1500       # 한 줄씩 실행 기록 상한
FNAME = "main.py"


class _OutputLimit(BaseException):
    pass


class _SoftTimeout(BaseException):
    pass


class _Out(io.TextIOBase):
    def __init__(self):
        self.parts = []
        self.n = 0

    def write(self, s):
        if not isinstance(s, str):
            raise TypeError("write() argument must be str, not " + type(s).__name__)
        self.n += len(s)
        if self.n > MAX_OUT:
            self.parts.append(s[: max(0, MAX_OUT - (self.n - len(s)))])
            raise _OutputLimit()
        self.parts.append(s)
        return len(s)

    def getvalue(self):
        return "".join(self.parts)

    def writable(self):
        return True

    def flush(self):
        pass


_SKIP = (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, type)


def _snap(frame):
    loc = frame.f_globals if frame.f_code.co_name == "<module>" else frame.f_locals
    d = {}
    for k, v in list(loc.items()):
        if k.startswith("__") or isinstance(v, _SKIP):
            continue
        try:
            r = repr(v)
        except Exception:
            r = "?"
        if len(r) > 90:
            r = r[:87] + "..."
        d[k] = [type(v).__name__, r]
    return d


def _hot(linecount):
    hot = sorted(linecount.items(), key=lambda kv: -kv[1])
    top = hot[0][1] if hot else 0
    return sorted(ln for ln, c in hot if c >= max(2, top // 3))


def features(code):
    try:
        tree = ast.parse(code)
    except Exception:
        return None
    f = {"ifexp": False, "while": False, "for": False, "continue": False, "break": False,
         "print_max_args": 0, "assigned": []}
    names = set()

    def targets(t):
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                targets(e)

    for n in ast.walk(tree):
        if isinstance(n, ast.IfExp):
            f["ifexp"] = True
        elif isinstance(n, ast.While):
            f["while"] = True
        elif isinstance(n, ast.For):
            f["for"] = True
        elif isinstance(n, ast.Continue):
            f["continue"] = True
        elif isinstance(n, ast.Break):
            f["break"] = True
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print":
            f["print_max_args"] = max(f["print_max_args"], len(n.args))
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                targets(t)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            targets(n.target)
    f["assigned"] = sorted(names)
    return f


def run_user(code, stdin, record=False, soft_limit=5.0):
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    res = {"stdout": "", "error": None, "steps": None, "truncatedSteps": False, "ms": 0,
           "features": features(code)}
    linecache.cache[FNAME] = (len(code), None, code.splitlines(True), FNAME)

    try:
        compiled = compile(code, FNAME, "exec")
    except SyntaxError as e:
        res["error"] = {
            "type": type(e).__name__, "msg": e.msg, "line": e.lineno, "col": e.offset,
            "endLine": getattr(e, "end_lineno", None), "endCol": getattr(e, "end_offset", None),
            "tb": "".join(traceback.format_exception_only(e)), "phase": "compile",
        }
        return json.dumps(res)

    if stdin and not stdin.endswith("\n"):
        stdin += "\n"
    out = _Out()
    old = (sys.stdin, sys.stdout, sys.stderr)
    sys.stdin, sys.stdout, sys.stderr = io.StringIO(stdin), out, out

    steps = [] if record else None
    linecount = {}
    counter = [0]
    deadline = time.monotonic() + soft_limit

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != FNAME:
            return None
        if event == "line":
            ln = frame.f_lineno
            linecount[ln] = linecount.get(ln, 0) + 1
            counter[0] += 1
            if (counter[0] & 127) == 0 and time.monotonic() > deadline:
                raise _SoftTimeout()
            if steps is not None:
                if len(steps) < MAX_STEPS:
                    steps.append({"line": ln, "vars": _snap(frame), "out": out.n,
                                  "fn": frame.f_code.co_name})
                else:
                    res["truncatedSteps"] = True
        return tracer

    ns = {"__name__": "__main__", "__builtins__": builtins}
    t0 = time.monotonic()
    last_frame_vars = None
    sys.settrace(tracer)
    try:
        exec(compiled, ns)
    except _SoftTimeout:
        sys.settrace(None)
        loop_lines = _hot(linecount)
        res["error"] = {"type": "Timeout", "msg": "실행이 %g초 안에 끝나지 않음" % soft_limit,
                        "line": loop_lines[0] if loop_lines else None, "loopLines": loop_lines,
                        "tb": "", "phase": "run"}
    except _OutputLimit:
        sys.settrace(None)
        loop_lines = _hot(linecount)
        res["error"] = {"type": "OutputLimit", "msg": "출력이 너무 많음 (%d자 초과)" % MAX_OUT,
                        "line": loop_lines[0] if loop_lines else None, "loopLines": loop_lines,
                        "tb": "", "phase": "run"}
    except SystemExit:
        pass
    except BaseException as e:
        sys.settrace(None)
        frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == FNAME]
        tb = ""
        if frames:
            tb = "Traceback (most recent call last):\n" + "".join(traceback.StackSummary.from_list(frames).format())
        tb += "".join(traceback.format_exception_only(e))
        last = frames[-1] if frames else None
        res["error"] = {
            "type": type(e).__name__, "msg": str(e),
            "line": last.lineno if last else None,
            "col": (last.colno + 1) if last and last.colno is not None else None,
            "endLine": last.end_lineno if last else None,
            "endCol": (last.end_colno + 1) if last and last.end_colno is not None else None,
            "tb": tb, "phase": "run",
        }
    finally:
        sys.settrace(None)
        sys.stdin, sys.stdout, sys.stderr = old

    res["ms"] = int((time.monotonic() - t0) * 1000)
    res["stdout"] = out.getvalue()
    if steps is not None:
        final = {"line": None, "vars": {}, "out": out.n, "fn": "<module>"}
        for k, v in ns.items():
            if k.startswith("__") or isinstance(v, _SKIP):
                continue
            try:
                r = repr(v)
            except Exception:
                r = "?"
            final["vars"][k] = [type(v).__name__, r if len(r) <= 90 else r[:87] + "..."]
        if not res["truncatedSteps"]:
            steps.append(final)
        res["steps"] = steps
    return json.dumps(res)
