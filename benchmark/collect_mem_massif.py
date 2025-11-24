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

# -----------------------------
# Listas de algoritmos
# -----------------------------

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

# -----------------------------
# Args / utilitários
# -----------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Coleta uso de memória (heap/stack) via Valgrind Massif para "
            "test_kem_mem ou test_sig_mem, com N execuções por algoritmo/operação, "
            "remoção de outliers por IQR e cálculo de média/std/IC 95%% t-Student."
        )
    )
    parser.add_argument(
        "binary",
        help="Caminho para o executável test_kem_mem ou test_sig_mem",
    )
    parser.add_argument(
        "-n",
        "--num-runs",
        type=int,
        default=20,
        help="Número de repetições por algoritmo/operação (default: 20)",
    )
    parser.add_argument(
        "--alg",
        type=str,
        help="Rodar somente um algoritmo específico (nome liboqs)",
    )
    return parser.parse_args()


def get_script_dir() -> str:
    if "__file__" in globals():
        return os.path.dirname(os.path.abspath(__file__))
    return os.getcwd()


# -----------------------------
# Estatística: t crítico 95%
# -----------------------------

def t_critical_95(df: int) -> float:
    """t crítico bicaudal 95% (alpha=0.05)."""
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
    return 1.960  # aproximação normal


def summary_with_ci(values: List[float]) -> Dict[str, float]:
    """Retorna dict com mean, std, ci_low, ci_high (IC 95% t-Student)."""
    n = len(values)
    mean = stats.mean(values)
    if n < 2:
        return {
            "mean": mean,
            "std": 0.0,
            "ci_low": mean,
            "ci_high": mean,
        }

    s = stats.stdev(values)  # desvio padrão amostral
    df = n - 1
    t = t_critical_95(df)
    margin = t * s / (n ** 0.5)
    return {
        "mean": mean,
        "std": s,
        "ci_low": mean - margin,
        "ci_high": mean + margin,
    }


# -----------------------------
# IQR / remoção de outliers
# -----------------------------

def iqr_mask(values: List[float]) -> List[bool]:
    """Máscara booleana indicando quais valores NÃO são outliers via IQR."""
    n = len(values)
    if n < 4:
        return [True] * n
    qs = stats.quantiles(values, n=4, method="inclusive")
    q1, q3 = qs[0], qs[2]
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return [(lower <= v <= upper) for v in values]


# -----------------------------
# Massif parsing
# -----------------------------

def parse_ms_print_output(ms_output: str) -> Tuple[int, int, int, int, int]:
    """
    Extrai (insts, maxBytes, maxHeap, extHeap, maxStack) do snapshot de pico
    do ms_print, usando lógica equivalente ao run_mem.py original.
    """
    lines = ms_output.splitlines()
    peak_index = -1

    # Descobrir qual snapshot é o 'peak'
    for line in lines:
        if line.startswith(" Detailed snapshots: ["):
            m = re.search(r"(\d+)\s+\(peak\)", line)
            if m:
                peak_index = int(m.group(1))
                break

    if peak_index < 0:
        raise RuntimeError("Não foi possível localizar o snapshot de pico em ms_print.")

    # Encontrar a linha do snapshot de pico
    peak_line = None
    prefix = f"{peak_index:>3d}"
    for line in lines:
        if line.startswith(prefix):
            peak_line = line
            break

    if peak_line is None:
        raise RuntimeError("Linha do snapshot de pico não encontrada em ms_print.")

    # Remover vírgulas e dividir em colunas
    tokens = peak_line.replace(",", "").split()
    # tokens = [snap_id, time(i), total(B), useful-heap(B), extra-heap(B), stacks(B)]
    if len(tokens) < 6:
        raise RuntimeError(f"Formato inesperado da linha de snapshot: {peak_line}")

    values = list(map(int, tokens[1:6]))
    insts, maxBytes, maxHeap, extHeap, maxStack = values
    return insts, maxBytes, maxHeap, extHeap, maxStack


def run_massif_once(binary: str, alg: str, op_code: int, workdir: str) -> Dict[str, int]:
    """
    Executa uma vez o binário sob valgrind/massif e retorna um dict com:
    insts, maxBytes, maxHeap, extHeap, maxStack (em bytes / instruções).
    """
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
            f"[ERROR] Valgrind/Massif falhou para {alg}, op={op_code}, "
            f"retcode={proc.returncode}",
            file=sys.stderr,
        )
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise RuntimeError("Falha ao executar valgrind/massif.")

    # Rodar ms_print no arquivo gerado
    proc2 = subprocess.run(
        ["ms_print", massif_out],
        cwd=workdir,
        capture_output=True,
        text=True,
    )

    if proc2.returncode != 0:
        print(
            f"[ERROR] ms_print falhou para {alg}, op={op_code}, "
            f"retcode={proc2.returncode}",
            file=sys.stderr,
        )
        print(proc2.stdout, file=sys.stderr)
        print(proc2.stderr, file=sys.stderr)
        raise RuntimeError("Falha ao executar ms_print.")

    insts, maxBytes, maxHeap, extHeap, maxStack = parse_ms_print_output(proc2.stdout)
    return {
        "insts": insts,
        "maxBytes": maxBytes,
        "maxHeap": maxHeap,
        "extHeap": extHeap,
        "maxStack": maxStack,
    }


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    args = parse_args()
    script_dir = get_script_dir()
    binary_path = os.path.abspath(args.binary)
    binary_dir = os.path.dirname(binary_path)
    binary_name = os.path.basename(binary_path)

    if not os.path.isfile(binary_path):
        print(f"[ERROR] Binário não encontrado: {binary_path}", file=sys.stderr)
        sys.exit(1)

    # Detecta se é KEM ou SIG pelo nome do binário
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
            "[ERROR] Não consegui inferir se é KEM ou SIG pelo nome do binário. "
            "O nome deve conter 'kem' ou 'sig'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Decide quais algoritmos rodar
    if args.alg:
        if args.alg not in alg_list:
            print(
                f"[ERROR] Algoritmo '{args.alg}' não está na lista para {mode}.\n"
                f"Válidos: {', '.join(alg_list)}",
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
        f"[*] Modo={mode}, binário={binary_name}, num_runs={args.num_runs}, "
        f"algorithms={algorithms}"
    )

    # Estrutura do JSON
    json_data: Dict[str, Any] = {
        "binary": binary_name,
        "mode": mode,
        "num_runs_requested": args.num_runs,
        "timestamp": ts,
        "results": {},
    }

    # CSV: uma linha por (algoritmo, operação)
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

            # Coleta bruta
            for _ in range(args.num_runs):
                res = run_massif_once(binary_path, alg, op_code, binary_dir)
                insts_vals.append(float(res["insts"]))
                maxBytes_vals.append(float(res["maxBytes"]) / (1024.0 * 1024.0))  # MB
                maxHeap_vals.append(float(res["maxHeap"]) / (1024.0 * 1024.0))    # MB
                extHeap_vals.append(float(res["extHeap"]) / (1024.0 * 1024.0))    # MB
                maxStack_vals.append(float(res["maxStack"]) / (1024.0 * 1024.0))  # MB

            n_raw = len(insts_vals)

            # Remoção de outliers usando maxBytes (total) como referência
            mask = iqr_mask(maxBytes_vals)
            n_filt = sum(mask)
            if n_filt < 3:
                # Se o filtro comeu demais, usa dados brutos
                mask = [True] * n_raw
                n_filt = n_raw

            insts_f = [v for v, keep in zip(insts_vals, mask) if keep]
            maxBytes_f = [v for v, keep in zip(maxBytes_vals, mask) if keep]
            maxHeap_f = [v for v, keep in zip(maxHeap_vals, mask) if keep]
            extHeap_f = [v for v, keep in zip(extHeap_vals, mask) if keep]
            maxStack_f = [v for v, keep in zip(maxStack_vals, mask) if keep]

            # Resumos estatísticos
            insts_s = summary_with_ci(insts_f)
            maxBytes_s = summary_with_ci(maxBytes_f)
            maxHeap_s = summary_with_ci(maxHeap_f)
            extHeap_s = summary_with_ci(extHeap_f)
            maxStack_s = summary_with_ci(maxStack_f)

            # Preenche JSON: lista bruta + summary
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

            # Linha para o CSV
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

    # Salva CSV
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Salva JSON
    with open(json_path, "w") as jf:
        json.dump(json_data, jf, indent=2)

    print(f"[OK] CSV salvo em:  {csv_path}")
    print(f"[OK] JSON salvo em: {json_path}")


if __name__ == "__main__":
    main()

