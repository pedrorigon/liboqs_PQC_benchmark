#!/usr/bin/env python3
import argparse
import glob
import os
import subprocess
import sys


def run_cmd(cmd, cwd=None, env=None):
    print(f"[*] Running: {' '.join(cmd)} (cwd={cwd or os.getcwd()})")
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        raise SystemExit(
            f"[ERROR] Command failed with code {result.returncode}: {' '.join(cmd)}"
        )


def ensure_venv(root_dir: str):
    if os.environ.get("LIBOQS_BENCH_VENV_ACTIVE") == "1":
        return

    venv_dir = os.path.join(root_dir, ".venv")
    venv_python = os.path.join(venv_dir, "bin", "python")

    if not os.path.exists(venv_dir):
        print(f"[*] Creating venv at {venv_dir} ...")
        run_cmd([sys.executable, "-m", "venv", venv_dir], cwd=root_dir)
        run_cmd([venv_python, "-m", "pip", "install", "--upgrade", "pip"], cwd=root_dir)
        run_cmd(
            [venv_python, "-m", "pip", "install", "matplotlib", "pandas"],
            cwd=root_dir,
        )

    env = os.environ.copy()
    env["LIBOQS_BENCH_VENV_ACTIVE"] = "1"
    script_path = os.path.abspath(__file__)
    args = [venv_python, script_path] + sys.argv[1:]
    print(f"[*] Re-executing inside venv: {' '.join(args)}")
    os.execve(venv_python, args, env)


def choose_isolated_core():
    try:
        aff = os.sched_getaffinity(0)
    except AttributeError:
        return None, None

    if not aff:
        return None, None

    sorted_cores = sorted(aff)
    chosen = None
    for c in sorted_cores:
        if c not in (0, 1):
            chosen = c
            break

    if chosen is None:
        chosen = sorted_cores[0]

    return chosen, aff


def setup_benchmark_cpu_mode():
    state = {
        "affinity_original": None,
        "turbo": None,
        "governors": {},
    }

    chosen_core, orig_aff = choose_isolated_core()
    if chosen_core is not None and orig_aff is not None:
        try:
            os.sched_setaffinity(0, {chosen_core})
            state["affinity_original"] = orig_aff
            print(f"[*] CPU affinity pinned to core {chosen_core}")
        except Exception as e:
            print(f"[WARN] Could not set CPU affinity: {e}")

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[WARN] Script is not running as root; will not change Turbo Boost/governor, only CPU affinity.")
        return state

    turbo_candidates = [
        "/sys/devices/system/cpu/intel_pstate/no_turbo",
        "/sys/devices/system/cpu/cpufreq/boost",
    ]
    for path in turbo_candidates:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    orig = f.read().strip()
                state["turbo"] = {"path": path, "original": orig}
                if path.endswith("no_turbo"):
                    new_val = "1"
                elif path.endswith("boost"):
                    new_val = "0"
                else:
                    new_val = None

                if new_val is not None and orig != new_val:
                    try:
                        with open(path, "w") as fw:
                            fw.write(new_val)
                        print(f"[*] Turbo Boost disabled at {path} (value={new_val})")
                    except PermissionError:
                        print(f"[WARN] No permission to write to {path}")
                break
            except PermissionError:
                print(f"[WARN] No permission to read {path}")
                continue

    cpu_glob = "/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor"
    for gov_path in glob.glob(cpu_glob):
        try:
            with open(gov_path, "r") as f:
                orig_gov = f.read().strip()
            state["governors"][gov_path] = orig_gov
            if orig_gov != "performance":
                try:
                    with open(gov_path, "w") as fw:
                        fw.write("performance")
                except PermissionError:
                    print(f"[WARN] No permission to write to {gov_path}")
        except FileNotFoundError:
            continue
        except PermissionError:
            print(f"[WARN] No permission to read {gov_path}")
            continue

    if state["governors"]:
        print("[*] CPU governors set to 'performance' where possible.")

    return state


def restore_benchmark_cpu_mode(state):
    if state and state.get("affinity_original") is not None:
        try:
            os.sched_setaffinity(0, state["affinity_original"])
            print("[*] CPU affinity restored to original state.")
        except Exception as e:
            print(f"[WARN] Could not restore CPU affinity: {e}")

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[WARN] Not running as root; nothing to restore for Turbo/governor.")
        return

    turbo_info = state.get("turbo") if state else None
    if turbo_info and os.path.exists(turbo_info["path"]):
        try:
            with open(turbo_info["path"], "w") as f:
                f.write(turbo_info["original"])
            print(
                f"[*] Turbo Boost restored "
                f"({turbo_info['path']}={turbo_info['original']})."
            )
        except PermissionError:
            print(f"[WARN] No permission to restore turbo at {turbo_info['path']}")

    govs = state.get("governors", {}) if state else {}
    for gov_path, orig_gov in govs.items():
        if not os.path.exists(gov_path):
            continue
        try:
            with open(gov_path, "w") as f:
                f.write(orig_gov)
        except PermissionError:
            print(f"[WARN] No permission to restore governor at {gov_path}")


def needs_build(liboqs_dir: str) -> bool:
    build_dir = os.path.join(liboqs_dir, "build")
    tests_dir = os.path.join(build_dir, "tests")

    if not os.path.isdir(build_dir):
        return True

    expected_bins = ["speed_kem", "speed_sig", "test_kem_mem", "test_sig_mem"]
    for bin_name in expected_bins:
        bin_path = os.path.join(tests_dir, bin_name)
        if not os.path.isfile(bin_path):
            return True

    return False


def fix_permissions_for_sudo_user(path: str):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return

    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")

    if not sudo_uid or not sudo_gid:
        print("[WARN] SUDO_UID/SUDO_GID not set; permissions were not fixed.")
        return

    uid = int(sudo_uid)
    gid = int(sudo_gid)

    print(f"[*] Recursively chown-ing {path} to UID={uid}, GID={gid}...")
    try:
        os.chown(path, uid, gid)
        for root, dirs, files in os.walk(path):
            for d in dirs:
                full = os.path.join(root, d)
                try:
                    os.chown(full, uid, gid)
                except PermissionError:
                    print(f"[WARN] No permission to chown {full}")
            for f in files:
                full = os.path.join(root, f)
                try:
                    os.chown(full, uid, gid)
                except PermissionError:
                    print(f"[WARN] No permission to chown {full}")
        print("[*] Permissions successfully adjusted.")
    except PermissionError as e:
        print(f"[WARN] Failed to adjust permissions for {path}: {e}")


def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    ensure_venv(root_dir)

    parser = argparse.ArgumentParser(
        description=(
            "Complete liboqs benchmark pipeline: speed (KEM/SIG), "
            "memory (Massif), charts and LaTeX tables, with CPU isolation "
            "during the measurement phase."
        )
    )
    parser.add_argument(
        "-n",
        "--num-runs",
        dest="num_runs",
        type=int,
        default=20,
        help="Number of runs/replications for each benchmark (default: 20)",
    )
    args = parser.parse_args()

    liboqs_dir = os.path.join(root_dir, "liboqs")

    if not os.path.exists(liboqs_dir):
        print("[*] Cloning liboqs (tag 0.15.0)...")
        run_cmd(
            [
                "git",
                "clone",
                "--branch",
                "0.15.0",
                "--depth",
                "1",
                "https://github.com/open-quantum-safe/liboqs.git",
                "liboqs",
            ],
            cwd=root_dir,
        )
    else:
        print("[*] liboqs directory already exists; skipping clone.")

    build_dir = os.path.join(liboqs_dir, "build")
    if needs_build(liboqs_dir):
        print("[*] Test binaries not found or build directory missing; running cmake+ninja...")
        os.makedirs(build_dir, exist_ok=True)
        run_cmd(
            ["cmake", "-GNinja", "-DOQS_ENABLE_KEM_HQC=ON", ".."],
            cwd=build_dir,
        )
        run_cmd(["ninja"], cwd=build_dir)
    else:
        print("[*] Existing build with expected binaries; skipping cmake/ninja.")

    cpu_state = setup_benchmark_cpu_mode()

    try:
        tests_dir = os.path.join(build_dir, "tests")
        speed_kem_exec = os.path.join(tests_dir, "speed_kem")
        speed_sig_exec = os.path.join(tests_dir, "speed_sig")

        bench_dir = os.path.join(root_dir, "benchmark")

        run_cmd(
            [
                sys.executable,
                os.path.join(bench_dir, "run_speed_kem_benchmark.py"),
                "-n",
                str(args.num_runs),
                "--exec",
                speed_kem_exec,
            ],
            cwd=bench_dir,
        )
        run_cmd(
            [
                sys.executable,
                os.path.join(bench_dir, "run_speed_sig_benchmark.py"),
                "-n",
                str(args.num_runs),
                "--exec",
                speed_sig_exec,
            ],
            cwd=bench_dir,
        )

        run_cmd(
            [
                sys.executable,
                os.path.join(bench_dir, "speed_kem_table_tex.py"),
            ],
            cwd=bench_dir,
        )
        run_cmd(
            [
                sys.executable,
                os.path.join(bench_dir, "speed_sig_table_tex.py"),
            ],
            cwd=bench_dir,
        )

        run_cmd(
            [
                sys.executable,
                os.path.join(bench_dir, "run_all_mem_bench.py"),
                "-n",
                str(args.num_runs),
            ],
            cwd=bench_dir,
        )

        run_cmd(
            [sys.executable, os.path.join(bench_dir, "mem_kem_chart.py")],
            cwd=bench_dir,
        )
        run_cmd(
            [sys.executable, os.path.join(bench_dir, "mem_sig_chart.py")],
            cwd=bench_dir,
        )

        print("\n[OK] Full pipeline (speed + memory + charts + LaTeX tables) completed.")
    finally:
        restore_benchmark_cpu_mode(cpu_state)
        fix_permissions_for_sudo_user(root_dir)


if __name__ == "__main__":
    main()
