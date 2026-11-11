#!/usr/bin/env python3
"""重いコマンド（RUN：`kloop graph`・`kloop resume`）を資源の上限の中で起動し、負荷を記録する。

    python3 scripts/run_capped.py --name implement -- kloop graph graphs/implement.toml
    python3 scripts/run_capped.py --stop implement            # 穏当に止める（kloop へ SIGINT 1 回）
    python3 scripts/run_capped.py --dry-run --name x -- true  # 掛ける上限と起動の形を表示するだけ

背景と判断は docs/decisions/ASSUMPTIONS.md の A-24。

1. 上限：systemd のユーザの scope（cgroup v2）で包む。子の claude・pytest・vitest・tsc まで同じ上限に入る。
   MemoryHigh を超えると scope の中だけで回収（ページキャッシュを含む）が起き、MemoryMax を超えると scope の中だけで
   OOM が起きる。CPUQuota で使えるコア数を、TasksMax でプロセス数を抑える。
   systemd のユーザのバスに繋がらないときは**起動しない**（上限の無いまま走らせると、落ちた時と同じ条件になる）。
   上限なしで走らせるなら `--allow-uncapped` を明示する
2. 優先度：nice 10・ionice best-effort 7（対話の操作を先にする）
3. 停止：`nohup … &` で起動すると SIGINT が無視されたまま始まり、kloop を穏当に止められない。起動の直前に SIGINT を
   既定へ戻す。このスクリプトが受けた INT / TERM / HUP と `--stop` は、kloop への SIGINT 1 回に換える
   （kloop は新しいノードを起こさず、走っているノードの終わりを待って CANCELLED で止まる。`kloop resume` で続きから）。
   2 回目は scope の全プロセスへ SIGTERM、その 30 秒後に残っていれば SIGKILL
4. 記録：interval 秒ごとに VM 全体と scope の負荷を `.kloop/load-monitor/<name>-<時刻>.tsv` へ追記する
   （行ごとに fsync。VM が落ちても直前まで残る）
5. 歯止め：VM 全体の空きメモリ（MemAvailable）が `--brake-pct` を `--brake-samples` 回続けて割ったら kloop へ SIGINT、
   `--kill-pct` を割り続けたら scope の全プロセスへ SIGTERM。ほかのセッションの負荷で VM ごと落ちるより、
   RUN を止めて `kloop resume` で続ける方を採る（`--no-brake` で外せる）

外部のパッケージに依存しない（標準ライブラリだけ）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / ".kloop" / "load-monitor"
EXIT_ENVIRONMENT_ERROR = 7  # kloop の ENVIRONMENT_ERROR と同じ番号
KILL_GRACE_SECONDS = 30.0

# 起動の直前に SIGINT を既定へ戻してから本体へ exec する（PID は変わらない）
SIGINT_RESET_SHIM = (
    "import os, signal, sys\n"
    "signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)

COLUMNS = (
    "time", "load1", "load5", "mem_avail_mb", "mem_avail_pct", "swap_used_mb", "cached_mb",
    "psi_cpu_some", "psi_mem_some", "psi_mem_full", "psi_io_full",
    "run_mem_mb", "run_mem_peak_mb", "run_cpu_cores", "run_pids", "run_oom_kills",
    "event", "top_rss",
)


# --------------------------------------------------------------------------- 測る
def read_meminfo() -> dict[str, int]:
    """/proc/meminfo（kB）。"""
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields:
            values[key] = int(fields[0])
    return values


def read_psi(resource: str) -> tuple[float, float]:
    """/proc/pressure/<resource> の some・full の avg10（%）。読めなければ -1。"""
    some = full = -1.0
    try:
        text = Path(f"/proc/pressure/{resource}").read_text()
    except OSError:
        return some, full
    for line in text.splitlines():
        kind, *pairs = line.split()
        avg10 = float(dict(p.split("=", 1) for p in pairs).get("avg10", -1))
        if kind == "some":
            some = avg10
        elif kind == "full":
            full = avg10
    return some, full


def top_rss(limit: int = 3) -> str:
    """VM 全体で常駐メモリの大きいプロセス（名前:MB）。見えるプロセスだけ。"""
    page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    found: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            rss_kb = int((entry / "statm").read_text().split()[1]) * page_kb
            name = (entry / "comm").read_text().strip()
        except (OSError, IndexError, ValueError):
            continue
        found.append((rss_kb, f"{name}[{entry.name}]"))
    found.sort(reverse=True)
    return ",".join(f"{name}:{kb // 1024}" for kb, name in found[:limit])


@dataclass
class ScopeStats:
    mem_mb: str = ""
    peak_mb: str = ""
    cpu_cores: str = ""
    pids: str = ""
    oom_kills: str = ""


class Scope:
    """RUN を包んだ cgroup（起動の後に /proc/<pid>/cgroup から見つける）。"""

    def __init__(self, unit: str | None) -> None:
        self.unit = unit
        self.path: Path | None = None
        self._last_usage: tuple[float, int] | None = None

    def locate(self, pid: int) -> None:
        if self.path is not None or self.unit is None:
            return
        try:
            rel = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[1]
        except (OSError, IndexError):
            return
        if rel.endswith(f"/{self.unit}.scope"):
            self.path = Path("/sys/fs/cgroup" + rel)

    def _read(self, name: str) -> str:
        assert self.path is not None
        return (self.path / name).read_text()

    def stats(self) -> ScopeStats:
        if self.path is None:
            return ScopeStats()
        out = ScopeStats()
        try:
            out.mem_mb = str(int(self._read("memory.current")) // 2**20)
            out.peak_mb = str(int(self._read("memory.peak")) // 2**20)
            out.pids = self._read("pids.current").strip()
            events = dict(line.split() for line in self._read("memory.events").splitlines())
            out.oom_kills = events.get("oom_kill", "")
            usage_usec = int(dict(line.split() for line in self._read("cpu.stat").splitlines())["usage_usec"])
        except (OSError, KeyError, ValueError):
            return out
        now = time.monotonic()
        if self._last_usage is not None:
            then, before = self._last_usage
            if now > then:
                out.cpu_cores = f"{(usage_usec - before) / 1e6 / (now - then):.2f}"
        self._last_usage = (now, usage_usec)
        return out

    def pids(self) -> list[int]:
        if self.path is None:
            return []
        try:
            return [int(p) for p in self._read("cgroup.procs").split()]
        except (OSError, ValueError):
            return []


# --------------------------------------------------------------------------- 上限
def parse_size(text: str, total_bytes: int) -> int:
    """`6G`・`512M`・`40%`（MemTotal に対する割合）をバイトへ。"""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT%]?)\s*", text, re.IGNORECASE)
    if not match:
        raise argparse.ArgumentTypeError(f"大きさの書き方が誤り: {text!r}（例 6G・512M・40%）")
    number, unit = float(match.group(1)), match.group(2).upper()
    if unit == "%":
        return int(total_bytes * number / 100)
    return int(number * {"": 1, "K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}[unit])


def user_bus_env() -> dict[str, str]:
    """systemctl --user・systemd-run --user に要る環境（XDG_RUNTIME_DIR が無い端末を補う）。"""
    env = dict(os.environ)
    runtime = f"/run/user/{os.getuid()}"
    if "XDG_RUNTIME_DIR" not in env and Path(runtime, "bus").exists():
        env["XDG_RUNTIME_DIR"] = runtime
    return env


def systemd_user_problem(env: dict[str, str]) -> str:
    """上限を掛けられない理由（掛けられるなら空）。"""
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        return "systemd-run・systemctl が無い"
    try:
        probe = subprocess.run(
            ["systemctl", "--user", "show", "--property=Version"],
            capture_output=True, text=True, timeout=15, env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"systemctl --user が応答しない: {exc}"
    if probe.returncode != 0:
        return "systemd のユーザのバスに繋がらない: " + (probe.stderr.strip() or probe.stdout.strip())
    uid = os.getuid()
    controllers = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/cgroup.controllers")
    try:
        have = set(controllers.read_text().split())
    except OSError as exc:
        return f"{controllers} が読めない: {exc}"
    missing = sorted({"memory", "cpu", "pids"} - have)
    if missing:
        return f"ユーザの systemd へ委譲されていない cgroup の制御: {', '.join(missing)}"
    return ""


# --------------------------------------------------------------------------- 本体
class Runner:
    def __init__(self, args: argparse.Namespace, command: list[str], capped: bool, env: dict[str, str]) -> None:
        self.args = args
        self.command = command
        self.capped = capped
        self.env = env
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        self.unit = f"ksd-{args.name}-{stamp}" if capped else None
        self.scope = Scope(self.unit)
        self.log_path = LOG_DIR / f"{args.name}-{stamp}.tsv"
        self.pid_path = LOG_DIR / f"{args.name}.pid"
        self.proc: subprocess.Popen[bytes] | None = None
        self.stop_requests = 0
        self.kill_deadline: float | None = None
        self.brake_streak = 0
        self.kill_streak = 0
        self.pending_events: list[str] = []
        self._log = None

    # ---------------------------------------------------------------- 起動の形
    def argv(self) -> list[str]:
        inner = [
            "nice", "-n", str(self.args.nice),
            "ionice", "-c", "2", "-n", "7",
            sys.executable, "-c", SIGINT_RESET_SHIM, *self.command,
        ]
        if not self.capped:
            return inner
        a = self.args
        return [
            "systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={self.unit}",
            "-p", f"MemoryHigh={a.mem_high // 2**20}M",
            "-p", f"MemoryMax={a.mem_max // 2**20}M",
            "-p", f"MemorySwapMax={a.swap_max // 2**20}M",
            "-p", f"CPUQuota={a.cpus * 100}%",
            "-p", f"TasksMax={a.tasks_max}",
            "--", *inner,
        ]

    def describe(self) -> str:
        a = self.args
        limits = (
            f"MemoryHigh={a.mem_high // 2**20}M MemoryMax={a.mem_max // 2**20}M "
            f"MemorySwapMax={a.swap_max // 2**20}M CPUQuota={a.cpus * 100}% TasksMax={a.tasks_max}"
            if self.capped else "上限なし（--allow-uncapped）"
        )
        brake = (
            "なし（--no-brake）" if a.no_brake else
            f"空き<{a.brake_pct}% が {a.brake_samples} 回続いたら kloop へ SIGINT、"
            f"空き<{a.kill_pct}% が {a.brake_samples} 回続いたら scope の全プロセスへ SIGTERM"
        )
        return (
            f"[run_capped] scope: {self.unit or '-'}\n"
            f"[run_capped] 上限: {limits}／nice {a.nice}・ionice best-effort 7\n"
            f"[run_capped] 歯止め: {brake}\n"
            f"[run_capped] 記録: {self.log_path.relative_to(ROOT)}（{a.interval:g} 秒ごと）\n"
            f"[run_capped] 止め方: python3 scripts/run_capped.py --stop {a.name}"
            f"（kloop へ SIGINT。もう一度で SIGTERM）\n"
            f"[run_capped] コマンド: {' '.join(self.command)}（cwd {Path.cwd()}）"
        )

    # ---------------------------------------------------------------- 記録
    def _write(self, row: list[str]) -> None:
        assert self._log is not None
        self._log.write("\t".join(row) + "\n")
        self._log.flush()
        os.fsync(self._log.fileno())

    def sample(self) -> float:
        """1 行書いて、VM 全体の空きメモリの割合（%）を返す。"""
        mem = read_meminfo()
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", 0)
        avail_pct = 100.0 * avail / total
        load1, load5 = os.getloadavg()[:2]
        cpu_some, _ = read_psi("cpu")
        mem_some, mem_full = read_psi("memory")
        _, io_full = read_psi("io")
        if self.proc is not None:
            self.scope.locate(self.proc.pid)
        s = self.scope.stats()
        events = ";".join(self.pending_events)
        self.pending_events.clear()
        self._write([
            dt.datetime.now().isoformat(timespec="seconds"),
            f"{load1:.2f}", f"{load5:.2f}",
            str(avail // 1024), f"{avail_pct:.1f}",
            str((mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) // 1024),
            str(mem.get("Cached", 0) // 1024),
            f"{cpu_some:.1f}", f"{mem_some:.1f}", f"{mem_full:.1f}", f"{io_full:.1f}",
            s.mem_mb, s.peak_mb, s.cpu_cores, s.pids, s.oom_kills,
            events, top_rss(),
        ])
        return avail_pct

    def note(self, event: str) -> None:
        self.pending_events.append(event)
        print(f"[run_capped] {dt.datetime.now().isoformat(timespec='seconds')} {event}", flush=True)

    # ---------------------------------------------------------------- 止める
    def signal_all(self, sig: signal.Signals) -> None:
        """scope の全プロセス（scope が無ければ子のプロセスグループ）へ送る。"""
        assert self.proc is not None
        pids = self.scope.pids()
        if pids:
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            return
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            pass

    def request_stop(self, reason: str) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.stop_requests += 1
        if self.stop_requests == 1:
            self.note(f"STOP1 {reason}: kloop へ SIGINT（新しいノードを起こさず、走っているノードの終わりを待って止まる）")
            try:
                os.kill(self.proc.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        elif self.kill_deadline is None:
            self.note(f"STOP2 {reason}: scope の全プロセスへ SIGTERM（{KILL_GRACE_SECONDS:g} 秒後に残れば SIGKILL）")
            self.signal_all(signal.SIGTERM)
            self.kill_deadline = time.monotonic() + KILL_GRACE_SECONDS

    def _on_signal(self, signum: int, _frame: object) -> None:
        self.request_stop(signal.Signals(signum).name)

    def check_brake(self, avail_pct: float) -> None:
        a = self.args
        if a.no_brake:
            return
        self.kill_streak = self.kill_streak + 1 if avail_pct < a.kill_pct else 0
        self.brake_streak = self.brake_streak + 1 if avail_pct < a.brake_pct else 0
        if self.kill_streak >= a.brake_samples:
            # 1 回目の停止を飛ばしてでも止める（走っているノードの終わりを待てない）
            self.stop_requests = max(self.stop_requests, 1)
            self.request_stop(f"BRAKE 空きメモリ {avail_pct:.1f}% < {a.kill_pct}%")
        elif self.brake_streak >= a.brake_samples and self.stop_requests == 0:
            self.request_stop(f"BRAKE 空きメモリ {avail_pct:.1f}% < {a.brake_pct}%")

    # ---------------------------------------------------------------- 走らせる
    def run(self) -> int:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        print(self.describe(), flush=True)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self._on_signal)
        with open(self.log_path, "a", encoding="utf-8") as log:
            self._log = log
            self._write(["#"] + [self.describe().replace("\n", " | ")])
            self._write(list(COLUMNS))
            # 子は別のセッションにする：端末の Ctrl-C が kloop へ直接届くと、ここから送る分と 2 回になる
            # 作業ディレクトリは呼び出し元のまま（kloop は cwd をプロジェクトの根とみなす。ほかのリポジトリの試験も包めるように）
            self.proc = subprocess.Popen(self.argv(), env=self.env, start_new_session=True)
            self.pid_path.write_text(json.dumps({
                "wrapper_pid": os.getpid(), "child_pid": self.proc.pid, "unit": self.unit,
                "log": str(self.log_path.relative_to(ROOT)), "command": self.command,
            }, ensure_ascii=False) + "\n")
            self.note(f"START pid={self.proc.pid}")
            try:
                while True:
                    try:
                        self.proc.wait(timeout=self.args.interval)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    self.check_brake(self.sample())
                    if self.kill_deadline is not None and time.monotonic() >= self.kill_deadline:
                        self.note("STOP3 SIGTERM の後も残っている: SIGKILL")
                        self.signal_all(signal.SIGKILL)
                        self.kill_deadline = float("inf")
            finally:
                code = self.proc.returncode
                if code is None:
                    code = self.proc.wait()
                exit_code = 128 - code if code < 0 else code
                self.note(f"END exit={exit_code}")
                self.sample()
                try:
                    self.pid_path.unlink()
                except FileNotFoundError:
                    pass
        return exit_code


def stop(name: str) -> int:
    pid_path = LOG_DIR / f"{name}.pid"
    try:
        info = json.loads(pid_path.read_text())
    except (OSError, ValueError):
        print(f"[run_capped] {pid_path.relative_to(ROOT)} が無い（`{name}` は走っていない）", file=sys.stderr)
        return 1
    pid = int(info["wrapper_pid"])
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        print(f"[run_capped] PID {pid} は既に終わっている（{pid_path.relative_to(ROOT)} が残っていた）", file=sys.stderr)
        return 1
    print(f"[run_capped] PID {pid} へ停止を頼んだ。経過は {info.get('log')} と起動時の出力先を見ること")
    return 0


def build_parser(total_bytes: int, cpus: int) -> argparse.ArgumentParser:
    size = lambda text: parse_size(text, total_bytes)  # noqa: E731
    p = argparse.ArgumentParser(
        description="重いコマンドを systemd のユーザの scope（cgroup v2）の上限の中で起動し、負荷を記録する",
    )
    p.add_argument("--name", default="run", help="scope・記録・停止に使う名前（英数字・-・_）")
    p.add_argument("--stop", metavar="NAME", help="NAME で起動したものを穏当に止める（kloop へ SIGINT）")
    p.add_argument("--dry-run", action="store_true", help="上限と起動の形を表示するだけで起動しない")
    p.add_argument("--mem-high", type=size, default="40%", help="MemoryHigh（既定 MemTotal の 40%%）")
    p.add_argument("--mem-max", type=size, default="50%", help="MemoryMax（既定 MemTotal の 50%%）")
    p.add_argument("--swap-max", type=size, default="1G", help="MemorySwapMax（既定 1G）")
    p.add_argument("--cpus", type=int, default=max(1, cpus // 2), help=f"使えるコア数（既定 {max(1, cpus // 2)}）")
    p.add_argument("--tasks-max", type=int, default=2048, help="プロセス・スレッドの数の上限（既定 2048）")
    p.add_argument("--nice", type=int, default=10, help="nice の値（既定 10）")
    p.add_argument("--interval", type=float, default=15.0, help="記録の間隔（秒。既定 15）")
    p.add_argument("--brake-pct", type=float, default=10.0, help="kloop へ SIGINT を送る VM 全体の空きメモリ（%%。既定 10）")
    p.add_argument("--kill-pct", type=float, default=5.0, help="scope の全プロセスへ SIGTERM を送る空きメモリ（%%。既定 5）")
    p.add_argument("--brake-samples", type=int, default=4, help="何回続けて割ったら動くか（既定 4）")
    p.add_argument("--no-brake", action="store_true", help="歯止めを外す")
    p.add_argument("--allow-uncapped", action="store_true", help="systemd のユーザのバスが無くても上限なしで起動する")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- の後に起動するコマンド")
    return p


def main(argv: list[str] | None = None) -> int:
    total_bytes = read_meminfo().get("MemTotal", 0) * 1024
    parser = build_parser(total_bytes, os.cpu_count() or 1)
    args = parser.parse_args(argv)
    if args.stop:
        return stop(args.stop)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("起動するコマンドを -- の後に書くこと")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error(f"--name は英数字・-・_ だけ: {args.name!r}")
    if not 0 < args.kill_pct <= args.brake_pct < 100:
        parser.error("0 < --kill-pct <= --brake-pct < 100 にすること")
    if args.mem_high > args.mem_max:
        parser.error("--mem-high は --mem-max 以下にすること")

    env = user_bus_env()
    problem = systemd_user_problem(env)
    if problem and not args.allow_uncapped:
        print(
            f"[run_capped] ENVIRONMENT_ERROR: 上限を掛けられない（{problem}）。\n"
            "  起動していない。上限なしで走らせるなら --allow-uncapped を付ける"
            "（落ちたときと同じ条件になる）",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT_ERROR
    if problem:
        print(f"[run_capped] WARNING: 上限なしで起動する（{problem}）", file=sys.stderr)

    runner = Runner(args, command, capped=not problem, env=env)
    if args.dry_run:
        print(runner.describe())
        print("[run_capped] argv: " + " ".join(
            "<SIGINT を既定へ戻す shim>" if a == SIGINT_RESET_SHIM else a for a in runner.argv()
        ))
        return 0
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
