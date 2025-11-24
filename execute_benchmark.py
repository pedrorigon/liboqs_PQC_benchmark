#!/usr/bin/env python3
import argparse
import glob
import os
import subprocess
import sys


def run_cmd(cmd, cwd=None, env=None):
    print(f"[*] Executando: {' '.join(cmd)} (cwd={cwd or os.getcwd()})")
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        raise SystemExit(
            f"[ERROR] Comando falhou com código {result.returncode}: {' '.join(cmd)}"
        )


def ensure_venv(root_dir: str):
    """
    Garante que estamos rodando dentro de um venv local (.venv).
    Na primeira execução:
      - cria o venv
      - instala matplotlib/pandas
      - reexecuta o próprio script usando o Python do venv.
    """
    if os.environ.get("LIBOQS_BENCH_VENV_ACTIVE") == "1":
        return

    venv_dir = os.path.join(root_dir, ".venv")
    venv_python = os.path.join(venv_dir, "bin", "python")

    if not os.path.exists(venv_dir):
        print(f"[*] Criando venv em {venv_dir} ...")
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
    print(f"[*] Reexecutando dentro do venv: {' '.join(args)}")
    os.execve(venv_python, args, env)


def choose_isolated_core():
    """
    Escolhe um core para isolamento:
      - tenta evitar 0 e 1
      - se não der, pega o menor disponível.
    Retorna (core_escolhido, afinidade_original).
    """
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
    """
    - Fixa afinidade em um único core (idealmente != 0 e != 1)
    - Desliga Turbo Boost (quando possível)
    - Ajusta governors para 'performance' (quando possível)
    Retorna um dicionário com o estado original para restauração.
    """
    state = {
        "affinity_original": None,
        "turbo": None,
        "governors": {},
    }

    # Afinidade de CPU (não requer root)
    chosen_core, orig_aff = choose_isolated_core()
    if chosen_core is not None and orig_aff is not None:
        try:
            os.sched_setaffinity(0, {chosen_core})
            state["affinity_original"] = orig_aff
            print(f"[*] Afinidade de CPU fixada no core {chosen_core}")
        except Exception as e:
            print(f"[WARN] Não foi possível fixar afinidade de CPU: {e}")

    # Ajuste de Turbo e governor requer root
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[WARN] Script não está rodando como root; "
              "não vou alterar Turbo Boost/governor, apenas afinidade de CPU.")
        return state

    # Detectar arquivo de turbo (Intel/AMD)
    turbo_candidates = [
        "/sys/devices/system/cpu/intel_pstate/no_turbo",  # Intel
        "/sys/devices/system/cpu/cpufreq/boost",          # AMD ou genérico
    ]
    for path in turbo_candidates:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    orig = f.read().strip()
                state["turbo"] = {"path": path, "original": orig}
                # Desligar turbo
                if path.endswith("no_turbo"):
                    new_val = "1"  # 1 = turbo off
                elif path.endswith("boost"):
                    new_val = "0"  # 0 = turbo off
                else:
                    new_val = None

                if new_val is not None and orig != new_val:
                    try:
                        with open(path, "w") as fw:
                            fw.write(new_val)
                        print(f"[*] Turbo Boost desativado em {path} (valor={new_val})")
                    except PermissionError:
                        print(f"[WARN] Sem permissão para escrever em {path}")
                break
            except PermissionError:
                print(f"[WARN] Sem permissão para ler {path}")
                continue

    # Ajustar governor para performance
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
                    print(f"[WARN] Sem permissão para escrever em {gov_path}")
        except FileNotFoundError:
            continue
        except PermissionError:
            print(f"[WARN] Sem permissão para ler {gov_path}")
            continue

    if state["governors"]:
        print("[*] Governors de CPU ajustados para 'performance' (quando possível).")

    return state


def restore_benchmark_cpu_mode(state):
    """
    Restaura afinidade, Turbo Boost e governors ao estado original.
    Chamado sempre no final (try/finally), mesmo em caso de erro ou Ctrl+C.
    """
    # Restaurar afinidade de CPU
    if state and state.get("affinity_original") is not None:
        try:
            os.sched_setaffinity(0, state["affinity_original"])
            print("[*] Afinidade de CPU restaurada ao estado original.")
        except Exception as e:
            print(f"[WARN] Não foi possível restaurar afinidade de CPU: {e}")

    # Se não for root, nada para restaurar de turbo/governor
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("[WARN] Não está rodando como root; nada a restaurar em Turbo/governor.")
        return

    # Restaurar turbo
    turbo_info = state.get("turbo") if state else None
    if turbo_info and os.path.exists(turbo_info["path"]):
        try:
            with open(turbo_info["path"], "w") as f:
                f.write(turbo_info["original"])
            print(
                f"[*] Turbo Boost restaurado "
                f"({turbo_info['path']}={turbo_info['original']})."
            )
        except PermissionError:
            print(f"[WARN] Sem permissão para restaurar turbo em {turbo_info['path']}")

    # Restaurar governors
    govs = state.get("governors", {}) if state else {}
    for gov_path, orig_gov in govs.items():
        if not os.path.exists(gov_path):
            continue
        try:
            with open(gov_path, "w") as f:
                f.write(orig_gov)
        except PermissionError:
            print(f"[WARN] Sem permissão para restaurar governor em {gov_path}")


def needs_build(liboqs_dir: str) -> bool:
    """
    Verifica se precisamos rodar cmake/ninja.
    Critério: se não existir build/ ou se faltarem os binários esperados em build/tests.
    """
    build_dir = os.path.join(liboqs_dir, "build")
    tests_dir = os.path.join(build_dir, "tests")

    # Se não existe build, precisa buildar
    if not os.path.isdir(build_dir):
        return True

    expected_bins = ["speed_kem", "speed_sig", "test_kem_mem", "test_sig_mem"]
    for bin_name in expected_bins:
        bin_path = os.path.join(tests_dir, bin_name)
        if not os.path.isfile(bin_path):
            return True

    return False


def fix_permissions_for_sudo_user(path: str):
    """
    Se o script foi rodado via sudo, ajusta o dono de 'path' recursivamente
    para o usuário original (SUDO_UID/SUDO_GID). Isso evita o 'cadeado'
    nos arquivos gerados pelo root.
    """
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        # Não é root => nada a fazer
        return

    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")

    if not sudo_uid or not sudo_gid:
        print("[WARN] SUDO_UID/SUDO_GID não encontrados; não ajustei permissões.")
        return

    uid = int(sudo_uid)
    gid = int(sudo_gid)

    print(f"[*] Ajustando dono de {path} recursivamente para UID={uid}, GID={gid}...")
    try:
        # Ajusta o diretório raiz
        os.chown(path, uid, gid)
        # Ajusta todo o conteúdo
        for root, dirs, files in os.walk(path):
            for d in dirs:
                full = os.path.join(root, d)
                try:
                    os.chown(full, uid, gid)
                except PermissionError:
                    print(f"[WARN] Sem permissão para chown em {full}")
            for f in files:
                full = os.path.join(root, f)
                try:
                    os.chown(full, uid, gid)
                except PermissionError:
                    print(f"[WARN] Sem permissão para chown em {full}")
        print("[*] Permissões ajustadas com sucesso.")
    except PermissionError as e:
        print(f"[WARN] Falha ao ajustar permissões em {path}: {e}")


def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    ensure_venv(root_dir)

    parser = argparse.ArgumentParser(
        description=(
            "Pipeline completo de benchmarks liboqs: speed (KEM/SIG), "
            "memória (Massif) e geração de gráficos de memória, "
            "com isolamento de CPU durante a fase de medição."
        )
    )
    parser.add_argument(
        "-n",
        "--num-runs",
        dest="num_runs",
        type=int,
        default=20,
        help="Número de execuções/replicações para cada benchmark (default: 20)",
    )
    args = parser.parse_args()

    liboqs_dir = os.path.join(root_dir, "liboqs")

    # 1) Clonar liboqs na tag 0.15.0 (se ainda não existir)
    if not os.path.exists(liboqs_dir):
        print("[*] Clonando liboqs (tag 0.15.0)...")
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
        print("[*] Diretório liboqs já existe, não vou clonar novamente.")

    # 2) Configurar e compilar liboqs com Ninja (HQC ativado) APENAS se necessário
    build_dir = os.path.join(liboqs_dir, "build")
    if needs_build(liboqs_dir):
        print("[*] Binários de teste não encontrados ou build inexistente; rodando cmake+ninja...")
        os.makedirs(build_dir, exist_ok=True)
        run_cmd(
            ["cmake", "-GNinja", "-DOQS_ENABLE_KEM_HQC=ON", ".."],
            cwd=build_dir,
        )
        run_cmd(["ninja"], cwd=build_dir)
    else:
        print("[*] Build existente com binários esperados; pulando cmake/ninja.")

    # 3) Depois de buildar, agora sim ativamos o modo benchmark (isolamento CPU)
    cpu_state = setup_benchmark_cpu_mode()

    try:
        # Caminhos dos executáveis speed_kem / speed_sig gerados em liboqs/build/tests
        tests_dir = os.path.join(build_dir, "tests")
        speed_kem_exec = os.path.join(tests_dir, "speed_kem")
        speed_sig_exec = os.path.join(tests_dir, "speed_sig")

        bench_dir = os.path.join(root_dir, "benchmark")

        # 4) Benchmarks de speed (KEM + SIG) usando os executáveis da liboqs/build/tests
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

        # 5) Benchmarks de memória (Massif) para KEM e SIG
        run_cmd(
            [
                sys.executable,
                os.path.join(bench_dir, "run_all_mem_bench.py"),
                "-n",
                str(args.num_runs),
            ],
            cwd=bench_dir,
        )

        # 6) Geração dos gráficos de memória (usando os CSVs mais recentes)
        run_cmd(
            [sys.executable, os.path.join(bench_dir, "mem_kem_chart.py")],
            cwd=bench_dir,
        )
        run_cmd(
            [sys.executable, os.path.join(bench_dir, "mem_sig_chart.py")],
            cwd=bench_dir,
        )

        print("\n[OK] Pipeline completo (speed + memória + gráficos) finalizado.")
    finally:
        # Restaura estado original da CPU, mesmo em caso de erro ou Ctrl+C
        restore_benchmark_cpu_mode(cpu_state)
        # Garante que TUDO dentro do projeto (incluindo CSVs em benchmark/) seja do usuário original
        fix_permissions_for_sudo_user(root_dir)


if __name__ == "__main__":
    main()

