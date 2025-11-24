#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import statistics as stats
import subprocess
import sys
from datetime import datetime
from typing import List, Tuple, Dict, Any

KEM_ALGS: List[str] = [
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

SIG_ALGS: List[str] = [
    "ML-DSA-44",
    "ML-DSA-65",
    "ML-DSA-87",
    "Falcon-512",
    "Falcon-1024",
    "Falcon-padded-512",
    "Falcon-padded-1024",
    "SPHINCS+-SHA2-128f-simple",
    "SPHINCS+-SHA2-128s-simple",
    "SPHINCS+-SHA2-192f-simple",
    "SPHINCS+-SHA2-192s-simple",
    "SPHINCS+-SHA2-256f-simple",
    "SPHINCS+-SHA2-256s-simple",
    "SPHINCS+-SHAKE-128f-simple",
    "SPHINCS+-SHAKE-128s-simple",
    "SPHINCS+-SHAKE-192f-simple",
    "SPHINCS+-SHAKE-192s-simple",
    "SPHINCS+-SHAKE-256f-simple",
    "SPHINCS+-SHAKE-256s-simple",
]

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collects heap/stack usage with Valgrind Massif for test_kem_mem or test_sig_mem, "
            "runs N times per algorithm/operation, removes outliers with IQR, and computes "
            "mean/std/95% confidence interval (t-Student)."
        )
    )
    parser.add_argument(
        "binary",
        help="Path to the test_kem_mem or test_sig_mem executable",
    )
    parser.add_argument(
        "-n",
        "--num-runs",
        type=int,
        default=20,
        help="Number of repetitions per algorithm/operation (default: 20)",
    )
    parser.add_argument(
        "--alg",
        type=str,
        help="Run only a specific algorithm (liboqs name)",
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


def parse_ms_print_output(ms_output: str) -> Tuple[int, int, int, int, int]:
    """Extract peak snapshot metrics (insts, maxBytes, maxHeap, extHeap, maxStack) from ms_print output."""
    lines = ms_output.splitlines()
    peak_index = -1

    for line in lines:
        if line.startswith(" Detailed snapshots: ["):
            m = re.search(r"(\d+)\s+\(peak\)", line)
            if m:
                peak_index = int(m.group(1))
                break

    if peak_index < 0:
        raise RuntimeError("Could not find peak snapshot in ms_print output.")

    peak_line = None
    prefix = f"{peak_index:>3d}"
    for line in lines:
        if line.startswith(prefix):
            peak_line = line
            break

    if peak_line is None:
        raise RuntimeError("Peak snapshot line not found in ms_print output.")

    tokens = peak_line.replace(",", "").split()
    if len(tokens) < 6:
        raise RuntimeError(f"Unexpected snapshot line format: {peak_line}")

    values = list(map(int, tokens[1:6]))
    insts, maxBytes, maxHeap, extHeap, maxStack = values
    return insts, maxBytes, maxHeap, extHeap, maxStack


def run_massif_once(binary: str, alg: str, op_code: int, workdir: str) -> Dict[str, int]:
    massif_out = os.path.join(workdir, "valgrind-out")

    cmd = [
        "valgrind",
        "--tool=massif",
        "--stacks=yes",
        f"--massif-out-file={massif_out}",
        binary,
        alg,
        str(op_code),
    ]
    proc = subprocess.run(
        cmd,
        cwd=workdir,
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        print(
            f"[ERROR] Valgrind/Massif failed for {alg}, op={op_code}, "
            f"return code={proc.returncode}",
            file=sys.stderr,
        )
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise RuntimeError("Failed to execute valgrind/massif.")

    proc2 = subprocess.run(
        ["ms_print", massif_out],
        cwd=workdir,
        capture_output=True,
        text=True,
    )

    if proc2.returncode != 0:
        print(
            f"[ERROR] ms_print failed for {alg}, op={op_code}, "
            f"return code={proc2.returncode}",
            file=sys.stderr,
        )
        print(proc2.stdout, file=sys.stderr)
        print(proc2.stderr, file=sys.stderr)
        raise RuntimeError("Failed to execute ms_print.")

    insts, maxBytes, maxHeap, extHeap, maxStack = parse_ms_print_output(proc2.stdout)
    return {
        "insts": insts,
        "maxBytes": maxBytes,
        "maxHeap": maxHeap,
        "extHeap": extHeap,
        "maxStack": maxStack,
    }


def main() -> None:
    args = parse_args()
    script_dir = get_script_dir()
    binary_path = os.path.abspath(args.binary)
    binary_dir = os.path.dirname(binary_path)
    binary_name = os.path.basename(binary_path)

    if not os.path.isfile(binary_path):
        print(f"[ERROR] Binary not found: {binary_path}", file=sys.stderr)
        sys.exit(1)

    if "kem" in binary_name.lower():
        mode = "kem"
        alg_list = KEM_ALGS
        operations = [("keygen", 0), ("encaps", 1), ("decaps", 2)]
        results_dir = os.path.join(script_dir, "results_mem_kem")
        csv_prefix = "results_kem_mem_"
        json_prefix = "results_kem_mem_"
    elif "sig" in binary_name.lower():
        mode = "sig"
        alg_list = SIG_ALGS
        operations = [("keygen", 0), ("sign", 1), ("verify", 2)]
        results_dir = os.path.join(script_dir, "results_mem_sig")
        csv_prefix = "results_sig_mem_"
        json_prefix = "results_sig_mem_"
    else:
        print(
            "[ERROR] Could not infer KEM or SIG from binary name. "
            "The name must contain 'kem' or 'sig'.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.alg:
        if args.alg not in alg_list:
            print(
                f"[ERROR] Algorithm '{args.alg}' is not valid for {mode}.\n"
                f"Valid: {', '.join(alg_list)}",
                file=sys.stderr,
            )
            sys.exit(1)
        algorithms = [args.alg]
    else:
        algorithms = alg_list

    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(results_dir, f"{csv_prefix}{ts}.csv")
    json_path = os.path.join(results_dir, f"{json_prefix}{ts}.json")

    print(
        f"[*] mode={mode}, binary={binary_name}, num_runs={args.num_runs}, "
        f"algorithms={algorithms}"
    )

    json_data: Dict[str, Any] = {
        "binary": binary_name,
        "mode": mode,
        "num_runs_requested": args.num_runs,
        "timestamp": ts,
        "results": {},
    }

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

    for alg in algorithms:
        json_data["results"][alg] = {}
        for op_name, op_code in operations:
            print(f"  -> {alg} / {op_name} ({args.num_runs} runs)")

            insts_vals: List[float] = []
            maxBytes_vals: List[float] = []
            maxHeap_vals: List[float] = []
            extHeap_vals: List[float] = []
            maxStack_vals: List[float] = []

            for _ in range(args.num_runs):
                res = run_massif_once(binary_path, alg, op_code, binary_dir)
                insts_vals.append(float(res["insts"]))
                maxBytes_vals.append(float(res["maxBytes"]) / (1024.0 * 1024.0))
                maxHeap_vals.append(float(res["maxHeap"]) / (1024.0 * 1024.0))
                extHeap_vals.append(float(res["extHeap"]) / (1024.0 * 1024.0))
                maxStack_vals.append(float(res["maxStack"]) / (1024.0 * 1024.0))

            n_raw = len(insts_vals)

            mask = iqr_mask(maxBytes_vals)
            n_filt = sum(mask)
            if n_filt < 3:
                mask = [True] * n_raw
                n_filt = n_raw

            insts_f = [v for v, keep in zip(insts_vals, mask) if keep]
            maxBytes_f = [v for v, keep in zip(maxBytes_vals, mask) if keep]
            maxHeap_f = [v for v, keep in zip(maxHeap_vals, mask) if keep]
            extHeap_f = [v for v, keep in zip(extHeap_vals, mask) if keep]
            maxStack_f = [v for v, keep in zip(maxStack_vals, mask) if keep]

            insts_s = summary_with_ci(insts_f)
            maxBytes_s = summary_with_ci(maxBytes_f)
            maxHeap_s = summary_with_ci(maxHeap_f)
            extHeap_s = summary_with_ci(extHeap_f)
            maxStack_s = summary_with_ci(maxStack_f)

            json_data["results"][alg][op_name] = {
                "raw": {
                    "insts": insts_vals,
                    "maxBytes_mb": maxBytes_vals,
                    "maxHeap_mb": maxHeap_vals,
                    "extHeap_mb": extHeap_vals,
                    "maxStack_mb": maxStack_vals,
                },
                "summary": {
                    "num_runs_raw": n_raw,
                    "num_runs_filtered": n_filt,
                    "insts": insts_s,
                    "maxBytes_mb": maxBytes_s,
                    "maxHeap_mb": maxHeap_s,
                    "extHeap_mb": extHeap_s,
                    "maxStack_mb": maxStack_s,
                },
            }

            row = {
                "algorithm": alg,
                "operation": op_name,
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

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with open(json_path, "w") as jf:
        json.dump(json_data, jf, indent=2)

    print(f"[OK] CSV saved to:  {csv_path}")
    print(f"[OK] JSON saved to: {json_path}")


if __name__ == "__main__":
    main()
