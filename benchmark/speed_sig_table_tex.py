#!/usr/bin/env python3
import glob
import math
import os

import pandas as pd


def latex_escape(text: str) -> str:
    replacements = {
        "_": r"\_",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "$": r"\$",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def format_time_us(value: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    v_str = f"{value:.1f}"
    if "." in v_str:
        int_part, frac_part = v_str.split(".")
    else:
        int_part, frac_part = v_str, None
    grouped = "{:,}".format(int(int_part)).replace(",", r"\,")
    return f"{grouped}.{frac_part}" if frac_part is not None else grouped


def format_cycles_k(value: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    k_val = int(round(value / 1000.0))
    grouped = "{:,}".format(k_val).replace(",", r"\,")
    return f"{grouped}k"


def find_latest_csv(results_dir: str, prefix: str) -> str:
    pattern = os.path.join(results_dir, f"{prefix}_*.csv")
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise SystemExit(f"[ERROR] No CSV files found matching {pattern}")
    return candidates[-1]


def format_algorithm_name(alg: str) -> str:
    # Agora apenas escapa, sem mudar SPHINCS+-...
    return latex_escape(alg)


def build_sig_table(df: pd.DataFrame) -> str:
    algorithms = list(dict.fromkeys(df["algorithm"]))
    operations = ["keypair", "sign", "verify"]

    lines: list[str] = []
    lines.append(r"\begin{table}[h]")
    lines.append(r"  \begin{center}")
    lines.append(r"  \caption{Desempenho das operações de Assinaturas PQC: latência e ciclos de CPU.}")
    lines.append(r"  \scriptsize")
    lines.append(r"  \setlength{\tabcolsep}{3pt}")
    lines.append(r"  \resizebox{0.8\textwidth}{!}{%")
    lines.append(r"    \begin{tabular}{@{}l rr rr rr@{}}")
    lines.append(r"      \toprule")
    lines.append(
        r"      Algoritmo                            & \multicolumn{2}{c}{KeyGen}            & \multicolumn{2}{c}{Sign}             & \multicolumn{2}{c}{Verify}           \\"
    )
    lines.append(r"      \cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}")
    lines.append(
        r"                                          & Latência ± $\sigma$ (µs)    & Ciclos (k)       & Latência ± $\sigma$ (µs)    & Ciclos (k)       & Latência ± $\sigma$ (µs)    & Ciclos (k)       \\"
    )
    lines.append(r"      \midrule")

    for alg in algorithms:
        row_cells = [format_algorithm_name(alg)]
        for op in operations:
            sub = df[(df["algorithm"] == alg) & (df["operation"] == op)]
            if sub.empty:
                row_cells.extend(["--", "--"])
            else:
                row = sub.iloc[0]
                t_mean = format_time_us(row["time_us_mean"])
                t_std = format_time_us(row["time_us_std"])
                cycles = format_cycles_k(row["cycles_mean"])
                row_cells.extend([f"{t_mean} ± {t_std}", cycles])
        lines.append("      " + " & ".join(row_cells) + r" \\")

    lines.append(r"      \bottomrule")
    lines.append(r"    \end{tabular}%")
    lines.append(r"  }")
    lines.append(r"  \label{tab:signature_perf}")
    lines.append(r"  \end{center}")
    lines.append(r"  \legend{Fonte: O autor}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.join(script_dir, "results_speed_sig")
    csv_path = find_latest_csv(results_dir, "results_speed_sig")
    print(f"[*] Using SIG CSV: {csv_path}")
    df = pd.read_csv(csv_path)
    latex_table = build_sig_table(df)
    out_path = os.path.join(results_dir, "speed_sig_table.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(latex_table + "\n")
    print(f"[*] LaTeX SIG table written to {out_path}")


if __name__ == "__main__":
    main()

