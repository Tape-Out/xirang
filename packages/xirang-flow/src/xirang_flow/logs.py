"""从工具的一堆输出里挑出人要看的那几行。"""


def first_err(log: str) -> str:
    """挑一行给人看。**错优先于警告**，不管谁排在前面。

    原来是「第一条含 Error 或 Warning 的行」，于是一条无害的警告排在前面就能把
    真正的错整个挡住：`plic` 的 pending 加宽成两个字之后报的是组合环（G0032），
    显示出来的却是「Field not defined: none」这句与失败毫无关系的警告，
    照着它查半天查不到东西。
    """
    lines = [ln.strip() for ln in log.splitlines()]
    for want in ("Error", "Warning"):
        for ln in lines:
            if want in ln:
                return ln[:160]
    return "编译没过"


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
