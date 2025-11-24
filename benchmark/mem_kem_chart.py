#!/usr/bin/env python3
import os
import glob
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

script_dir = os.path.dirname(os.path.abspath(__file__))
results_dir = os.path.join(script_dir, "results_mem_kem")

if not os.path.isdir(results_dir):
    raise SystemExit(f"[ERROR] Results directory not found: {results_dir}")

pattern = os.path.join(results_dir, "results_kem_mem_*.csv")
csv_files = glob.glob(pattern)

if not csv_files:
    raise SystemExit(f"[ERROR] No CSV files found matching {pattern}")

csv_file = max(csv_files, key=os.path.getmtime)
print(f"[*] Using newest KEM CSV: {csv_file}")

df = pd.read_csv(csv_file)

algorithms_to_plot = [
    "ML-KEM-512", "ML-KEM-768", "ML-KEM-1024",
    "HQC-128", "HQC-192", "HQC-256",
    "BIKE-L1", "BIKE-L3", "BIKE-L5",
    "FrodoKEM-640-AES", "FrodoKEM-640-SHAKE",
    "FrodoKEM-976-AES", "FrodoKEM-976-SHAKE",
    "FrodoKEM-1344-AES", "FrodoKEM-1344-SHAKE",
]


def disp_name(alg: str) -> str:
    return alg


title_map = {
    "keygen": "Geração de Chaves",
    "encaps": "Encapsulação",
    "decaps": "Decapsulação",
}

operations = ["keygen", "encaps", "decaps"]

for op in operations:
    heap_vals = []
    stack_vals = []
    heap_low_err = []
    heap_high_err = []
    stack_low_err = []
    stack_high_err = []
    names = []

    for alg in algorithms_to_plot:
        row = df[(df["algorithm"] == alg) & (df["operation"] == op)]
        if row.empty:
            continue
        row = row.iloc[0]

        heap = row["maxHeap_mean_mb"]
        stack = row["maxStack_mean_mb"]

        names.append(disp_name(alg))
        heap_vals.append(heap)
        stack_vals.append(stack)

        heap_low = row["maxHeap_ci_low_mb"]
        heap_high = row["maxHeap_ci_high_mb"]
        heap_low_err.append(heap - heap_low)
        heap_high_err.append(heap_high - heap)

        stack_low = row["maxStack_ci_low_mb"]
        stack_high = row["maxStack_ci_high_mb"]
        stack_low_err.append(stack - stack_low)
        stack_high_err.append(stack_high - stack)

    heap_arr = np.array(heap_vals)
    stack_arr = np.array(stack_vals)

    heap_err = np.vstack([heap_low_err, heap_high_err])
    stack_err = np.vstack([stack_low_err, stack_high_err])

    x = np.arange(len(names))
    width = 0.35

    fig, ax = plt.subplots(figsize=(12, 6))

    ax.bar(
        x - width / 2,
        heap_arr,
        width,
        label="Heap",
        yerr=heap_err,
        capsize=5,
        edgecolor="black",
    )
    ax.bar(
        x + width / 2,
        stack_arr,
        width,
        label="Stack",
        yerr=stack_err,
        capsize=5,
        edgecolor="black",
    )

    ax.set_title(
        f"Uso de Memória (Heap vs. Stack) — {title_map[op]}",
        fontsize=18,
    )
    ax.set_ylabel("Memória (MB)", fontsize=18)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=90, ha="center", fontsize=16)
    ax.tick_params(axis="y", labelsize=16)
    ax.grid(True, which="both", linestyle="--", linewidth=0.5, axis="y")
    ax.legend(fontsize=16)

    plt.tight_layout()

    base = f"memory_usage_{op}"
    for ext in ("png", "svg", "pdf"):
        fname = os.path.join(results_dir, f"{base}.{ext}")
        plt.savefig(fname, dpi=300, bbox_inches="tight")
        print(f"Saved {fname}")
