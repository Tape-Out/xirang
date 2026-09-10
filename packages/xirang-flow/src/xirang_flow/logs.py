"""从工具的一堆输出里挑出人要看的那几行。"""


def first_err(log: str) -> str:
    hits = (ln.strip() for ln in log.splitlines()
            if "Error" in ln or "Warning" in ln)
    return next(hits, "编译没过")[:160]


def tail(o: str) -> str:
    # 先挑失败那几行。只取末尾会把更早的失败截掉——一次跑出三条，
    # 报告里只剩最后一条，人就以为只错了那一处。
    lines = [x.strip() for x in o.strip().splitlines() if x.strip()]
    if not lines:
        return "没有输出"
    hits = [x for x in lines if x.startswith(("FAIL", "TIMEOUT", "Error"))]
    s = " / ".join(hits[:3] if hits else lines[-2:])
    if len(hits) > 3:
        s += f"（另有 {len(hits) - 3} 条）"
    return s[:400]
