#!/usr/bin/env python3
import argparse
import csv
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from statistics import mean, stdev
from datetime import datetime

ALGORITHMS = [
    "ML-KEM-512",
    "ML-KEM-768",
    "ML-KEM-1024",
    "HQC-128",
    "HQC-192",
    "HQC-256",
    "BIKE-L1",
    "BIKE-L3",
    "BIKE-L5",
    "FrodoKEM-640-AES",
    "FrodoKEM-640-SHAKE",
    "FrodoKEM-976-AES",
    "FrodoKEM-976-SHAKE",
    "FrodoKEM-1344-AES",
    "FrodoKEM-1344-SHAKE",
]

OPERATIONS = ["keygen", "encaps", "decaps"]

T_CRIT_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


def iqr_filter_indices(values):
    n = len(values)
    if n < 4:
        return list(range(n))
    sorted_vals = sorted(values)
    q1 = percentile(sorted_vals, 25)
    q3 = percentile(sorted_vals, 75)
    iqr = q3 - q1
    if iqr == 0:
        return list(range(n))
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    keep = [i for i, v in enumerate(values) if lower <= v <= upper]
    return keep


def t_crit_95(df):
    if df <= 0:
        return float("nan")
    if df in T_CRIT_95:
        return T_CRIT_95[df]
    return 1.96


def parse_speed_kem_output(text):
    ops = {}
    sizes = {}
    lines = text.splitlines()
    for line in lines:
        m = re.match(r"^(keygen|encaps|decaps)\s*\|(.*)$", line)
        if m:
            op = m.group(1)
            rest = [x.strip() for x in m.group(2).split("|")]
            if len(rest) < 6:
                continue
            iterations = int(rest[0])
            total_time_s = float(rest[1])
            time_us_mean = float(rest[2])
            cycles_mean_str = rest[4].replace(" ", "")
            cycles_mean = int(cycles_mean_str)
            ops[op] = {
                "iterations": iterations,
                "total_time_s": total_time_s,
                "time_us_mean": time_us_mean,
                "cycles_mean": cycles_mean,
            }
        if "public key bytes:" in line:
            m2 = re.search(
                r"public key bytes:\s*(\d+),\s*ciphertext bytes:\s*(\d+),\s*secret key bytes:\s*(\d+),\s*shared secret key bytes:\s*(\d+),\s*NIST level:\s*([0-9]+)",
                line,
            )
            if m2:
                sizes = {
                    "public_key_bytes": int(m2.group(1)),
                    "ciphertext_bytes": int(m2.group(2)),
                    "secret_key_bytes": int(m2.group(3)),
                    "shared_secret_bytes": int(m2.group(4)),
                    "nist_level": int(m2.group(5)),
                }
    return ops, sizes


def run_benchmarks(exec_path, repeats, algorithms, output_csv_path):
    stats = defaultdict(lambda: defaultdict(lambda: {
        "time_us": [],
        "cycles": [],
        "iterations": [],
        "total_time_s": [],
    }))
    sizes_map = {}
    for alg in algorithms:
        print(f"\n=== Algorithm: {alg} ===")
        for r in range(1, repeats + 1):
            print(f"Run {r}/{repeats} ...", end="", flush=True)
            try:
                result = subprocess.run(
                    [exec_path, "-i", "-d", "1", alg],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                print(" ERROR")
                sys.stderr.write(
                    f"[ERROR] speed_kem failed for {alg} run {r} (exit code {e.returncode})\n"
                )
                sys.stderr.write(e.stdout + "\n" + e.stderr + "\n")
                continue
            ops, sizes = parse_speed_kem_output(result.stdout)
            if sizes:
                sizes_map[alg] = sizes
            missing_ops = [op for op in OPERATIONS if op not in ops]
            if missing_ops:
                print(" ERROR (missing ops: " + ", ".join(missing_ops) + ")")
                sys.stderr.write(
                    f"[WARN] Missing operations for {alg} run {r}: {', '.join(missing_ops)}\n"
                )
                continue
            for op in OPERATIONS:
                rec = ops[op]
                s = stats[alg][op]
                s["iterations"].append(rec["iterations"])
                s["total_time_s"].append(rec["total_time_s"])
                s["time_us"].append(rec["time_us_mean"])
                s["cycles"].append(rec["cycles_mean"])
            print(" ok")
    summary_rows = []
    for alg in algorithms:
        for op in OPERATIONS:
            if alg not in stats or op not in stats[alg]:
                continue
            s = stats[alg][op]
            time_vals = s["time_us"]
            cycles_vals = s["cycles"]
            it_vals = s["iterations"]
            tt_vals = s["total_time_s"]
            n_raw = len(time_vals)
            if n_raw == 0:
                continue
            keep_idx = iqr_filter_indices(time_vals)
            if not keep_idx:
                keep_idx = list(range(n_raw))
            n = len(keep_idx)
            time_f = [time_vals[i] for i in keep_idx]
            cycles_f = [cycles_vals[i] for i in keep_idx]
            it_f = [it_vals[i] for i in keep_idx]
            tt_f = [tt_vals[i] for i in keep_idx]
            mean_time = mean(time_f)
            mean_cycles = mean(cycles_f)
            mean_it = mean(it_f)
            mean_tt = mean(tt_f)
            if n > 1:
                std_time = stdev(time_f)
                std_cycles = stdev(cycles_f)
                df = n - 1
                tcrit = t_crit_95(df)
                se_time = std_time / math.sqrt(n)
                se_cycles = std_cycles / math.sqrt(n)
                ci_time_low = mean_time - tcrit * se_time
                ci_time_high = mean_time + tcrit * se_time
                ci_cycles_low = mean_cycles - tcrit * se_cycles
                ci_cycles_high = mean_cycles + tcrit * se_cycles
            else:
                std_time = 0.0
                std_cycles = 0.0
                ci_time_low = mean_time
                ci_time_high = mean_time
                ci_cycles_low = mean_cycles
                ci_cycles_high = mean_cycles
            sizes = sizes_map.get(alg, {})
            summary_rows.append({
                "algorithm": alg,
                "operation": op,
                "n_raw": n_raw,
                "n_used": n,
                "mean_iterations": mean_it,
                "mean_total_time_s": mean_tt,
                "time_us_mean": mean_time,
                "time_us_std": std_time,
                "time_us_ci95_low": ci_time_low,
                "time_us_ci95_high": ci_time_high,
                "cycles_mean": mean_cycles,
                "cycles_std": std_cycles,
                "cycles_ci95_low": ci_cycles_low,
                "cycles_ci95_high": ci_cycles_high,
                "public_key_bytes": sizes.get("public_key_bytes"),
                "ciphertext_bytes": sizes.get("ciphertext_bytes"),
                "secret_key_bytes": sizes.get("secret_key_bytes"),
                "shared_secret_bytes": sizes.get("shared_secret_bytes"),
                "nist_level": sizes.get("nist_level"),
            })
    fieldnames = [
        "algorithm",
        "operation",
        "n_raw",
        "n_used",
        "mean_iterations",
        "mean_total_time_s",
        "time_us_mean",
        "time_us_std",
        "time_us_ci95_low",
        "time_us_ci95_high",
        "cycles_mean",
        "cycles_std",
        "cycles_ci95_low",
        "cycles_ci95_high",
        "public_key_bytes",
        "ciphertext_bytes",
        "secret_key_bytes",
        "shared_secret_bytes",
        "nist_level",
    ]
    with open(output_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)
    print(f"\nSummary data written to: {output_csv_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Run liboqs ./speed_kem multiple times and aggregate results."
    )
    parser.add_argument(
        "-n",
        "--repeats",
        type=int,
        default=20,
        help="Number of repetitions per algorithm (default: 20)",
    )
    parser.add_argument(
        "--exec",
        dest="exec_path",
        default=None,
        help="Path to speed_kem executable (default: ./speed_kem in script directory)",
    )
    parser.add_argument(
        "--alg",
        dest="alg",
        default=None,
        help="Run only this algorithm (default: run all predefined algorithms)",
    )
    args = parser.parse_args()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.exec_path is None:
        exec_path = os.path.join(script_dir, "speed_kem")
    else:
        exec_path = args.exec_path
    if not os.path.isfile(exec_path):
        sys.stderr.write(f"[ERROR] speed_kem executable not found at {exec_path}\n")
        sys.exit(1)
    results_dir = os.path.join(script_dir, "results_speed_kem")
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_csv_path = os.path.join(results_dir, f"results_speed_kem_{ts}.csv")
    if args.alg:
        algorithms = [args.alg]
    else:
        algorithms = ALGORITHMS
    run_benchmarks(exec_path, args.repeats, algorithms, output_csv_path)


if __name__ == "__main__":
    main()

