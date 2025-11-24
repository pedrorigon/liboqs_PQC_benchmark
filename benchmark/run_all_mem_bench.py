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
            "Roda coleta de memória (Valgrind Massif) para TODOS os KEM e "
            "TODAS as SIG usando collect_mem_massif.py.\n"
            "Agora: faz N execuções independentes (cada uma com -n 1) e, ao final, "
            "agrega os N CSVs em um único CSV final com média, std, IQR e IC 95%."
        )
    )
    parser.add_argument(
        "-n",
        "--num-runs",
        type=int,
        default=20,
        help="Número de execuções independentes (repetições) por algoritmo/operação (default: 20)",
    )
    return parser.parse_args()


def get_script_dir() -> str:
    if "__file__" in globals():
        return os.path.dirname(os.path.abspath(__file__))
    return os.getcwd()


# ----------------------------------------------------------------------
# Mesmas funções estatísticas usadas em collect_mem_massif.py
# (copiadas para manter o mesmo comportamento de IQR + IC 95% t-Student)
# ----------------------------------------------------------------------


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


# ----------------------------------------------------------------------
# Agregação dos N CSVs "brutos" em um único CSV final (KEM ou SIG)
# ----------------------------------------------------------------------


def aggregate_mem_results(results_dir: str, prefix: str, num_runs: int) -> None:
    """
    Lê os N CSVs mais recentes em results_dir com nome prefix*.csv,
    trata cada linha (algorithm, operation) como uma amostra independente,
    aplica:
      - remoção de outliers (IQR) usando maxBytes como referência,
      - média, std e IC 95% t-Student
    e escreve um novo CSV final com o MESMO formato do collect_mem_massif.py,
    apagando os CSV/JSON temporários usados.
    """
    pattern = os.path.join(results_dir, f"{prefix}*.csv")
    csv_files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

    if not csv_files:
        print(f"[WARN] Nenhum CSV encontrado em {results_dir} com prefixo {prefix}, nada para agregar.")
        return

    # Usa os N mais recentes (ou menos, se não houver N)
    used_files = csv_files[:num_runs]
    if len(used_files) < num_runs:
        print(
            f"[WARN] Foram encontrados apenas {len(used_files)} CSVs, "
            f"mas num_runs={num_runs}. Usando todos os disponíveis."
        )

    print(f"[*] Agregando {len(used_files)} CSV(s) em {results_dir} (prefixo={prefix})")

    # data[(alg, op)] = dict de listas
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
                # Cada CSV (rodado com -n 1) traz uma única amostra em *_mean_mb
                d["insts"].append(float(row["insts_mean"]))
                d["maxBytes"].append(float(row["maxBytes_mean_mb"]))
                d["maxHeap"].append(float(row["maxHeap_mean_mb"]))
                d["extHeap"].append(float(row["extHeap_mean_mb"]))
                d["maxStack"].append(float(row["maxStack_mean_mb"]))

    # Preparar linhas agregadas
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

    # Ordena por algoritmo / operação só para deixar o CSV estável
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

    # Salva CSV final agregado
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_csv = os.path.join(results_dir, f"{prefix}{ts}.csv")

    os.makedirs(results_dir, exist_ok=True)
    with open(final_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] CSV agregado final salvo em: {final_csv}")

    # Remove CSV/JSON temporários usados
    for fpath in used_files:
        try:
            base = os.path.splitext(os.path.basename(fpath))[0]  # ex: results_kem_mem_20251123_075913
            json_path = os.path.join(results_dir, base + ".json")
            os.remove(fpath)
            if os.path.exists(json_path):
                os.remove(json_path)
        except OSError as e:
            print(f"[WARN] Não foi possível remover temporário {fpath}: {e}")

    print("[OK] CSV/JSON temporários removidos.")


# ----------------------------------------------------------------------
# Execução dos binários test_kem_mem e test_sig_mem N vezes (cada uma -n 1)
# ----------------------------------------------------------------------


def run_collect(script_dir: str, binary_name: str, num_runs: int) -> None:
    """
    Para o binário indicado (test_kem_mem ou test_sig_mem):
      - roda collect_mem_massif.py num_runs vezes, cada vez com -n 1
      - em seguida agrega os num_runs CSVs gerados em um único CSV final.
    """
    # Projeto raiz = um nível acima de benchmark/
    project_root = os.path.abspath(os.path.join(script_dir, os.pardir))

    # Binários em liboqs/build/tests
    binary_path = os.path.join(
        project_root,
        "liboqs",
        "build",
        "tests",
        binary_name,
    )

    if not os.path.isfile(binary_path):
        print(f"[WARN] Binário não encontrado, pulando: {binary_path}")
        return

    collect_script = os.path.join(script_dir, "collect_mem_massif.py")
    if not os.path.isfile(collect_script):
        print(f"[ERROR] collect_mem_massif.py não encontrado em {script_dir}")
        sys.exit(1)

    # Descobre diretório de resultados e prefixo de arquivo dependendo se é KEM ou SIG
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
            f"[ERROR] Não consegui inferir se {binary_name} é KEM ou SIG (nome não contém 'kem' ou 'sig').",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(results_dir, exist_ok=True)

    # 1) Rodar N vezes o collect_mem_massif, cada vez com -n 1
    for i in range(num_runs):
        cmd = [
            sys.executable,
            collect_script,
            binary_path,  # argumento posicional "binary"
            "-n",
            "1",
        ]
        print(f"[*] ({mode}) Execução {i+1}/{num_runs}: {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=script_dir)
        if proc.returncode != 0:
            print(
                f"[ERROR] collect_mem_massif.py falhou para {binary_name} "
                f"na execução {i+1} com código {proc.returncode}",
                file=sys.stderr,
            )
            # se falhar, não tenta agregar
            return

    # 2) Agregar os N CSVs produzidos em um único CSV final
    aggregate_mem_results(results_dir, prefix, num_runs)


def main() -> None:
    args = parse_args()
    script_dir = get_script_dir()

    # 1) KEM
    print("\n=== KEM: test_kem_mem ===")
    run_collect(script_dir, "test_kem_mem", args.num_runs)

    # 2) SIG
    print("\n=== SIG: test_sig_mem ===")
    run_collect(script_dir, "test_sig_mem", args.num_runs)

    print("\n[OK] Execução completa de KEM e SIG (memória) com agregação por N execuções.")


if __name__ == "__main__":
    main()

