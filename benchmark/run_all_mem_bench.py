#!/usr/bin/env python3
import argparse
import csv
import glob
import os
import statistics as stats
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Tuple, Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run memory collection (Valgrind Massif) for all KEM and SIG algorithms using collect_mem_massif.py. "
            "Performs N independent executions (each with -n 1) and aggregates the CSVs into one file with mean, "
            "std, IQR filtering, and 95% confidence intervals."
        )
    )
    parser.add_argument(
        "-n",
        "--num-runs",
        type=int,
        default=20,
        help="Number of independent executions per algorithm/operation (default: 20)",
    )
    return parser.parse_args()


def get_script_dir() -> str:
    if "__file__" in globals():
        return os.path.dirname(os.path.abspath(__file__))
    return os.getcwd()


def t_critical_95(df: int) -> float:
    """Two-tailed 95% t critical value (alpha=0.05)."""
    if df <= 1:
        return 0.0

    table = {
        2: 4.303,
        3: 3.182,
        4: 2.776,
        5: 2.571,
        6: 2.447,
        7: 2.365,
        8: 2.306,
        9: 2.262,
        10: 2.228,
        11: 2.201,
        12: 2.179,
        13: 2.160,
        14: 2.145,
        15: 2.131,
        16: 2.120,
        17: 2.110,
        18: 2.101,
        19: 2.093,
        20: 2.086,
        21: 2.080,
        22: 2.074,
        23: 2.069,
        24: 2.064,
        25: 2.060,
        26: 2.056,
        27: 2.052,
        28: 2.048,
        29: 2.045,
        30: 2.042,
    }

    if df in table:
        return table[df]
    if df <= 40:
        return 2.021
    if df <= 60:
        return 2.000
    return 1.960


def summary_with_ci(values: List[float]) -> Dict[str, float]:
    """Return mean, std, ci_low, ci_high (95% CI, t-Student)."""
    n = len(values)
    mean = stats.mean(values)
    if n < 2:
        return {
            "mean": mean,
            "std": 0.0,
            "ci_low": mean,
            "ci_high": mean,
        }

    s = stats.stdev(values)
    df = n - 1
    t = t_critical_95(df)
    margin = t * s / (n ** 0.5)
    return {
        "mean": mean,
        "std": s,
        "ci_low": mean - margin,
        "ci_high": mean + margin,
    }


def iqr_mask(values: List[float]) -> List[bool]:
    """Boolean mask indicating which values are kept after IQR filtering."""
    n = len(values)
    if n < 4:
        return [True] * n
    qs = stats.quantiles(values, n=4, method="inclusive")
    q1, q3 = qs[0], qs[2]
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return [(lower <= v <= upper) for v in values]


def aggregate_mem_results(results_dir: str, prefix: str, num_runs: int) -> None:
    """
    Read the N most recent CSVs in results_dir matching prefix*.csv, treat each line
    (algorithm, operation) as an independent sample, filter outliers via IQR using
    maxBytes as reference, compute mean/std/95% CI, and write a final CSV matching
    the collect_mem_massif.py format while removing the temporary CSV/JSON files.
    """
    pattern = os.path.join(results_dir, f"{prefix}*.csv")
    csv_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

    if not csv_files:
        print(f"[WARN] No CSV files found in {results_dir} with prefix {prefix}; nothing to aggregate.")
        return

    used_files = csv_files[:num_runs]
    if len(used_files) < num_runs:
        print(
            f"[WARN] Found only {len(used_files)} CSVs but num_runs={num_runs}. Using all available files."
        )

    print(f"[*] Aggregating {len(used_files)} CSV(s) in {results_dir} (prefix={prefix})")

    data: Dict[Tuple[str, str], Dict[str, List[float]]] = {}

    for fpath in used_files:
        with open(fpath, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                alg = row["algorithm"]
                op = row["operation"]
                key = (alg, op)
                if key not in data:
                    data[key] = {
                        "insts": [],
                        "maxBytes": [],
                        "maxHeap": [],
                        "extHeap": [],
                        "maxStack": [],
                    }

                d = data[key]
                d["insts"].append(float(row["insts_mean"]))
                d["maxBytes"].append(float(row["maxBytes_mean_mb"]))
                d["maxHeap"].append(float(row["maxHeap_mean_mb"]))
                d["extHeap"].append(float(row["extHeap_mean_mb"]))
                d["maxStack"].append(float(row["maxStack_mean_mb"]))

    fieldnames = [
        "algorithm",
        "operation",
        "num_runs_raw",
        "num_runs_filtered",
        "insts_mean",
        "insts_std",
        "insts_ci_low",
        "insts_ci_high",
        "maxBytes_mean_mb",
        "maxBytes_std_mb",
        "maxBytes_ci_low_mb",
        "maxBytes_ci_high_mb",
        "maxHeap_mean_mb",
        "maxHeap_std_mb",
        "maxHeap_ci_low_mb",
        "maxHeap_ci_high_mb",
        "extHeap_mean_mb",
        "extHeap_std_mb",
        "extHeap_ci_low_mb",
        "extHeap_ci_high_mb",
        "maxStack_mean_mb",
        "maxStack_std_mb",
        "maxStack_ci_low_mb",
        "maxStack_ci_high_mb",
    ]

    rows: List[Dict[str, Any]] = []

    for (alg, op), metrics in sorted(data.items(), key=lambda x: (x[0][0], x[0][1])):
        maxBytes_vals = metrics["maxBytes"]
        n_raw = len(maxBytes_vals)

        mask = iqr_mask(maxBytes_vals)
        n_filt = sum(mask)
        if n_filt < 3:
            mask = [True] * n_raw
            n_filt = n_raw

        def filt(lst: List[float]) -> List[float]:
            return [v for v, keep in zip(lst, mask) if keep]

        insts_f = filt(metrics["insts"])
        maxBytes_f = filt(metrics["maxBytes"])
        maxHeap_f = filt(metrics["maxHeap"])
        extHeap_f = filt(metrics["extHeap"])
        maxStack_f = filt(metrics["maxStack"])

        insts_s = summary_with_ci(insts_f)
        maxBytes_s = summary_with_ci(maxBytes_f)
        maxHeap_s = summary_with_ci(maxHeap_f)
        extHeap_s = summary_with_ci(extHeap_f)
        maxStack_s = summary_with_ci(maxStack_f)

        row = {
            "algorithm": alg,
            "operation": op,
            "num_runs_raw": n_raw,
            "num_runs_filtered": n_filt,
            "insts_mean": insts_s["mean"],
            "insts_std": insts_s["std"],
            "insts_ci_low": insts_s["ci_low"],
            "insts_ci_high": insts_s["ci_high"],
            "maxBytes_mean_mb": maxBytes_s["mean"],
            "maxBytes_std_mb": maxBytes_s["std"],
            "maxBytes_ci_low_mb": maxBytes_s["ci_low"],
            "maxBytes_ci_high_mb": maxBytes_s["ci_high"],
            "maxHeap_mean_mb": maxHeap_s["mean"],
            "maxHeap_std_mb": maxHeap_s["std"],
            "maxHeap_ci_low_mb": maxHeap_s["ci_low"],
            "maxHeap_ci_high_mb": maxHeap_s["ci_high"],
            "extHeap_mean_mb": extHeap_s["mean"],
            "extHeap_std_mb": extHeap_s["std"],
            "extHeap_ci_low_mb": extHeap_s["ci_low"],
            "extHeap_ci_high_mb": extHeap_s["ci_high"],
            "maxStack_mean_mb": maxStack_s["mean"],
            "maxStack_std_mb": maxStack_s["std"],
            "maxStack_ci_low_mb": maxStack_s["ci_low"],
            "maxStack_ci_high_mb": maxStack_s["ci_high"],
        }
        rows.append(row)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_csv = os.path.join(results_dir, f"{prefix}{ts}.csv")

    os.makedirs(results_dir, exist_ok=True)
    with open(final_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] Aggregated CSV saved to: {final_csv}")

    for fpath in used_files:
        try:
            base = os.path.splitext(os.path.basename(fpath))[0]
            json_path = os.path.join(results_dir, base + ".json")
            os.remove(fpath)
            if os.path.exists(json_path):
                os.remove(json_path)
        except OSError as e:
            print(f"[WARN] Could not remove temporary file {fpath}: {e}")

    print("[OK] Temporary CSV/JSON files removed.")


def run_collect(script_dir: str, binary_name: str, num_runs: int) -> None:
    """
    For the given binary (test_kem_mem or test_sig_mem):
      - run collect_mem_massif.py num_runs times, each with -n 1
      - aggregate the generated CSVs into a single final CSV.
    """
    project_root = os.path.abspath(os.path.join(script_dir, os.pardir))

    binary_path = os.path.join(
        project_root,
        "liboqs",
        "build",
        "tests",
        binary_name,
    )

    if not os.path.isfile(binary_path):
        print(f"[WARN] Binary not found, skipping: {binary_path}")
        return

    collect_script = os.path.join(script_dir, "collect_mem_massif.py")
    if not os.path.isfile(collect_script):
        print(f"[ERROR] collect_mem_massif.py not found in {script_dir}")
        sys.exit(1)

    if "kem" in binary_name.lower():
        results_dir = os.path.join(script_dir, "results_mem_kem")
        prefix = "results_kem_mem_"
        mode = "KEM"
    elif "sig" in binary_name.lower():
        results_dir = os.path.join(script_dir, "results_mem_sig")
        prefix = "results_sig_mem_"
        mode = "SIG"
    else:
        print(
            f"[ERROR] Could not infer whether {binary_name} is KEM or SIG (name must contain 'kem' or 'sig').",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(results_dir, exist_ok=True)

    for i in range(num_runs):
        cmd = [
            sys.executable,
            collect_script,
            binary_path,
            "-n",
            "1",
        ]
        print(f"[*] ({mode}) Run {i+1}/{num_runs}: {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=script_dir)
        if proc.returncode != 0:
            print(
                f"[ERROR] collect_mem_massif.py failed for {binary_name} "
                f"on run {i+1} with code {proc.returncode}",
                file=sys.stderr,
            )
            return

    aggregate_mem_results(results_dir, prefix, num_runs)


def main() -> None:
    args = parse_args()
    script_dir = get_script_dir()

    print("\n=== KEM: test_kem_mem ===")
    run_collect(script_dir, "test_kem_mem", args.num_runs)

    print("\n=== SIG: test_sig_mem ===")
    run_collect(script_dir, "test_sig_mem", args.num_runs)

    print("\n[OK] Completed KEM and SIG memory runs with aggregation.")


if __name__ == "__main__":
    main()
