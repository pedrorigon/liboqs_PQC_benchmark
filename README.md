# liboqs_PQC_benchmark

Benchmark pipeline for Post-Quantum Cryptography using [liboqs](https://openquantumsafe.org/). The `execute_benchmark.py` entrypoint clones liboqs on demand, builds the test binaries, tunes the host for stable measurements, runs speed and memory benchmarks, and generates charts—everything in one command.

## What the pipeline does
- Creates/uses a local Python virtualenv (`.venv`) and installs Python deps automatically.
- Clones `liboqs` (tag `0.15.0`) if it is not present; no submodules are required.
- Builds the liboqs test binaries with CMake + Ninja.
- Tunes the host (CPU affinity, disables turbo, sets governors to `performance` where permitted). This step benefits from running with `sudo`; without root, only CPU affinity is applied.
- Runs speed benchmarks (`speed_kem`, `speed_sig`) with repetition and outlier filtering.
- Runs memory benchmarks with Valgrind Massif, aggregates multiple runs, and writes CSV/JSON.
- Generates heap vs. stack plots for KEM and signature algorithms.
- Restores original CPU settings and fixes file ownership when executed with `sudo`.

## Requirements
- Python 3.8+ (venv is created automatically).
- Git, CMake, Ninja, and a C compiler toolchain.
- Valgrind with `massif` and `ms_print`.
- `sudo` recommended to enable full host tuning (turbo off + performance governors).

## Quick start
Run the full pipeline (speed + memory + charts). Using `sudo` is recommended for host tuning stability:
```bash
sudo ./execute_benchmark.py -n 20
```
If you cannot run as root, the pipeline still runs but only sets CPU affinity.

### Key outputs
- Speed: `benchmark/results_speed_kem/results_speed_kem_<ts>.csv`, `benchmark/results_speed_sig/results_speed_sig_<ts>.csv`
- Memory: `benchmark/results_mem_kem/results_kem_mem_<ts>.{csv,json}`, `benchmark/results_mem_sig/results_sig_mem_<ts>.{csv,json}`
- Charts: saved under `benchmark/results_mem_kem/` and `benchmark/results_mem_sig/` as PNG, SVG, and PDF.

## Repository layout
- `execute_benchmark.py`: End-to-end pipeline with host tuning, build, benchmarks, and charts.
- `benchmark/run_speed_kem_benchmark.py`, `benchmark/run_speed_sig_benchmark.py`: Speed runners with IQR filtering and CI.
- `benchmark/run_all_mem_bench.py`: N independent Massif runs with aggregation.
- `benchmark/collect_mem_massif.py`: Single-run Massif collection.
- `benchmark/mem_kem_chart.py`, `benchmark/mem_sig_chart.py`: Chart generation from latest memory CSVs.
- `benchmark/results_*`: Output folders created on demand.

## Reproducibility notes
- Prefer running with `sudo` to lock governors and disable turbo; otherwise only affinity is applied.
- Keep the machine idle during runs and avoid thermal throttling.
- Use a consistent `-n` value for comparable datasets.
- All Python dependencies are installed in `.venv`; reruns reuse the same environment.

## Upstream rights
This toolkit builds and benchmarks [liboqs](https://openquantumsafe.org/); all rights for liboqs are reserved to the Open Quantum Safe project.

## How to cite
```bibtex
@software{liboqs_pqc_benchmark,
  title   = {liboqs\_PQC\_benchmark: Automated Memory and Speed Benchmarks for liboqs},
  author  = {Rigon, Pedro and contributors},
  year    = {2025},
  url     = {https://github.com/pedrorigon/liboqs_PQC_benchmark}
}
```
