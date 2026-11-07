#!/usr/bin/env python3
"""额度测量锚点（第 4 轮 F）：StopFailure / Notification hook，只写日志。

把「真正撞上限」(StopFailure error=rate_limit 等) 和「额度自动恢复」(Notification
quota_auto_resume_*) 的时刻追加一行 JSON 到 state/limit_events.jsonl，
同时附上当时 state/rate.json 里的 5 小时 / 7 天百分比快照，给 quota_fit 和周报当锚点。

规则：
- 永不往 stdout/stderr 输出（不进对话），永不失败：所有异常吞掉，始终 exit 0。
- 文件超过 5MB 先轮转到 limit_events_old.jsonl（不删）；_old 已存在就留在 时间戳.pid.随机 的名字下。
  并发安全（F3）：
  * 锁：state/limit_events.lock 上 flock(LOCK_EX|LOCK_NB)，每 10ms 重试，最多等 LOCK_WAIT 秒（0.3s）；
    拿不到锁就不轮转，只追加——绝不阻塞到 hook 的 10 秒超时。
  * 轮转第一步是 os.rename(原名 → 独一无二的归档名)：原子地把「当时原名下的那个文件」整体挪走，
    之后再也不碰原名（不 unlink 原名），所以别的进程新建的文件不可能被删。然后 os.link 到
    limit_events_old.jsonl（目标存在时原子失败、绝不覆盖），成功才删掉那个只有我们知道的归档名。
  * 追加：循环 os.write 直到写完；写不完就补一个换行；写之前若文件末尾不是换行也先补一个，
    保证每条记录都从新的一行开始。
- 字段来源：https://code.claude.com/docs/en/hooks （StopFailure input / Notification input，
  Claude Code 2.1.318 文档，2026-11-06 核实）。error_details 类型文档未写死（示例是字符串），
  这里字符串或对象都接受；quota_auto_resume_* 的 message/title 内容文档没给例子（待核实），原样截断保存。
- 状态目录：USAGE_SAVER_STATE，没有就用本套件的 state/。
"""
import json, os, sys, time, uuid

MAX_BYTES = 5 * 1024 * 1024
LOCK_WAIT = 0.3          # 最多等锁这么久（秒），远小于 settings 里的 timeout=10
LIMIT_ERRORS = {"rate_limit", "billing_error", "account_on_hold"}
QUOTA_PREFIX = "quota_auto_resume"


def _state_dir():
    return os.environ.get("USAGE_SAVER_STATE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


def _clip(v, n):
    """任意值 → 最长 n 字符的字符串（None 保持 None）。"""
    if v is None:
        return None
    if not isinstance(v, str):
        try:
            v = json.dumps(v, ensure_ascii=False, sort_keys=True)
        except Exception:
            v = repr(v)
    return v if len(v) <= n else v[:n] + "…"


def _rate_snapshot(state):
    """当时 rate.json 的百分比；读不到就全 None。"""
    out = {"rate_ts": None, "five_hour_pct": None, "five_hour_resets_at": None,
           "seven_day_pct": None, "seven_day_resets_at": None}
    try:
        with open(os.path.join(state, "rate.json"), encoding="utf-8") as f:
            r = json.load(f)
        out["rate_ts"] = r.get("ts")
        rl = r.get("rate_limits") or {}
        for k in ("five_hour", "seven_day"):
            w = rl.get(k) or {}
            out[k + "_pct"] = w.get("used_percentage")
            out[k + "_resets_at"] = w.get("resets_at")
    except Exception:
        pass
    return out


def _build(raw, state, now):
    try:
        p = json.loads(raw) if raw.strip() else {}
        if not isinstance(p, dict):
            p = {"_non_object": True}
    except Exception:
        p = {"_parse_error": True}
    ev = p.get("hook_event_name")
    row = {
        "ts": round(now, 3),
        "local_time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "event": ev,
        "session_id": p.get("session_id"),
        "cwd": p.get("cwd"),
        "transcript_path": p.get("transcript_path"),
    }
    if p.get("_parse_error") or p.get("_non_object"):
        row["parse_error"] = True
        row["raw_head"] = _clip(raw, 200)
    ntype = p.get("notification_type")
    if ev == "StopFailure" or "error" in p:
        err = p.get("error")
        row["error"] = err if isinstance(err, str) else _clip(err, 100)
        row["error_details"] = _clip(p.get("error_details"), 500)
        row["last_assistant_message"] = _clip(p.get("last_assistant_message"), 300)
        row["kind"] = "limit_hit" if row["error"] in LIMIT_ERRORS else "api_error"
    elif ev == "Notification" or ntype is not None:
        # settings 里已用 matcher 只订阅 quota_auto_resume_*；万一没加 matcher，这里再过滤一遍
        if isinstance(ntype, str) and not ntype.startswith(QUOTA_PREFIX):
            return None
        row["notification_type"] = ntype
        row["title"] = _clip(p.get("title"), 200)
        row["message"] = _clip(p.get("message"), 500)
        row["kind"] = "auto_resume" if ntype == "quota_auto_resume_fired" else (
            "auto_resume_stale" if ntype == "quota_auto_resume_stale" else (
                "auto_resume_disabled" if ntype == "quota_auto_resume_disabled" else "notification_unknown"))
    else:
        row["kind"] = "unknown"
    row.update(_rate_snapshot(state))
    return row


def _rotate(path):
    """超过 5MB 才轮转。只在持有文件锁时调用；就算锁失效、两个进程同时轮转，也不会删掉或覆盖任何数据：
    1) rename(path → 独一无二的名字)：整体挪走当时 path 下的那个文件（正在往它追加的进程写进的还是它）；
       之后完全不再碰 path，所以别人在 path 新建的文件不会被我们删掉。
    2) link(独一无二的名字 → limit_events_old.jsonl)：目标已存在会原子地失败，绝不覆盖；
       成功才 unlink 独一无二的名字（这个名字只有本进程知道，删掉它只是去掉一个多余的硬链接）。
       失败（_old 已存在、不支持硬链接）就让文件留在独一无二的名字下，它本身就是归档。"""
    try:
        if os.path.getsize(path) <= MAX_BYTES:
            return
    except OSError:
        return
    d = os.path.dirname(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    uniq = os.path.join(d, f"limit_events_old.{stamp}.{os.getpid()}.{uuid.uuid4().hex[:12]}.jsonl")
    if os.path.lexists(uniq):     # 48 位随机数，实际不会撞；撞了就这次不轮转
        return
    try:
        os.rename(path, uniq)
    except OSError:
        return                    # 原名已被别的进程转走，或改名失败：不轮转
    try:
        os.link(uniq, os.path.join(d, "limit_events_old.jsonl"))
    except OSError:
        return                    # _old 已存在 / 不支持硬链接：留在 uniq 名下
    try:
        os.unlink(uniq)
    except OSError:
        pass


def _lock(state):
    """非阻塞排它锁（fcntl.flock + LOCK_NB 短重试，最多 LOCK_WAIT 秒）。拿到返回 fd，拿不到返回 None。"""
    fd = None
    try:
        import fcntl
        fd = os.open(os.path.join(state, "limit_events.lock"), os.O_WRONLY | os.O_CREAT, 0o644)
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except (BlockingIOError, InterruptedError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
    except Exception:
        pass
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    return None


def _write_all(fd, data):
    """循环写完；返回 True=全部写完。中途返回 0 或抛 OSError 就返回 False（调用方负责补换行）。"""
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except InterruptedError:
            continue
        except OSError:
            return False
        if not n:
            return False
        view = view[n:]
    return True


def _append(path, line):
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)   # RDWR：要读最后一个字节
    try:
        # 上一条若残缺（比如进程被超时杀掉）没有换行，先补一个，保证这条从新的一行开始
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = b"\n" + line
        except OSError:
            pass
        if not _write_all(fd, line):
            _write_all(fd, b"\n")    # 写了半条：至少把这半条单独成行，下一条不被它污染
    finally:
        os.close(fd)


def main():
    raw = ""
    try:
        raw = sys.stdin.read(1_000_000)
    except Exception:
        pass
    state = _state_dir()
    row = _build(raw, state, time.time())
    if row is None:
        return
    os.makedirs(state, exist_ok=True)
    path = os.path.join(state, "limit_events.jsonl")
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    lk = _lock(state)
    try:
        if lk is not None:      # 拿不到锁（别人正在轮转或卡住）：这次不轮转，只追加
            _rotate(path)
        _append(path, line)
    finally:
        if lk is not None:
            os.close(lk)    # 关闭即释放 flock


if __name__ == "__main__":
    try:
        sys.stderr = open(os.devnull, "w")
    except Exception:
        pass
    try:
        main()
    except BaseException:
        pass
    os._exit(0)
