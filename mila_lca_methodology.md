# Mila Cluster LCA — Methodological Note

Environmental impacts of a subset of the Mila cluster (Montreal, Quebec) estimated
with **BoaviztAPI** (`POST /v1/server/` + `/v1/component/gpu`, local instance), with two
GPU-related API bugs corrected (see below). Reproducible via [`mila_lca.py`](./mila_lca.py).

## Inventory & node mapping

The submitted inventory gives model + GPU count + quantity. None of these models are
BoaviztAPI archetypes, so each is described as a custom server config. CPU model, core
count, RAM and local disk were cross-referenced from the
[Mila node profile table](https://docs.mila.quebec/technical_reference/clusters/mila/nodes/).

| Inventory line | Qty | Mila node | CPU (2 sockets) | RAM | Local disk | GPU |
|---|---|---|---|---|---|---|
| Dell XE8545 (4 GPU) | 29 | `cn-g[001-029]` | 2× AMD EPYC 7543 (32c) | 1024 GB | ~7 TB | 4× A100 80 GB SXM4 |
| Dell R6525 (4 GPU) | 4 | `cn-k[001-004]` | 2× AMD EPYC 7443 (24c) | 512 GB | ~3.6 TB | 4× A100 40 GB |
| Gigabyte G292-Z40 (4 GPU) | 1 | `cn-i001` | 2× AMD EPYC 7543 (32c) | 1024 GB | ~3.6 TB | 4× A100 80 GB (PCIe) |
| Gigabyte G292-Z40 (8 GPU) | 1 | `cn-j001` | 2× AMD EPYC 7543 (32c) | 1024 GB | ~3.6 TB | 8× RTX A6000 48 GB |
| NVIDIA DGX A100 640 GB (8 GPU) | 2 | `cn-d[003-004]` | 2× AMD EPYC 7742 (64c) | 2048 GB | ~28 TB | 8× A100 80 GB SXM4 |

Core counts come directly from the table (sockets × cores/socket); the specific CPU SKU
is the standard part shipped in each platform at that core count (Milan for the Dell/
Gigabyte AMD platforms, Rome EPYC 7742 for the DGX A100).

## Key modelling choices

- **GPU VRAM (A100 80 GB):** BoaviztAPI's GPU database only has the 40 GB A100. The 40 GB
  and 80 GB A100 share the same GA100 die; only the HBM stacks differ. We therefore use the
  `NVIDIA A100 SXM4 40GB` / `NVIDIA A100 PCIe 40GB` profiles and **override `vram` to 80**,
  so the die impact is correct and VRAM is scaled up.
- **RTX A6000 proxy (`cn-j001`):** the A6000 is absent from the database. An unknown GPU
  name silently falls back to the archetype's (H100-sized) die, which would grossly
  overestimate it. The A6000 uses the **GA102** die, so we use the in-DB `NVIDIA RTX A4500`
  (also GA102) as a physical proxy with `vram` set to 48 GB.
- **SXM vs PCIe:** SXM4 form factor used where the node has NVLink (`ampere,nvlink`); PCIe
  used for `cn-i001` (no NVLink flag).
- **RAM / disk:** RAM split into 64 GB (or 32 GB) DIMMs to match node memory; local disk
  modelled as SSD totalling the node's `TmpDisk`. Disk has a minor impact share.
- **Usage profile:** API defaults — 50% `time_workload`, `use_time_ratio` 1.0, ~35040 h
  (~4 yr) lifetime. Embedded impact **excludes end-of-life** (API limitation).

## BoaviztAPI corrections (important)

While modelling GPU-dense nodes we found **two BoaviztAPI bugs that severely undercount
GPU servers**. Both are corrected in `mila_lca.py`; without the fixes the fleet GWP is
understated by ~40%.

1. **GPU `units` ignored in embedded impact.** In `compute/impacts_computation.py`,
   `gpu_impact_embedded()` never multiplies by `gpu.units` (every other component does).
   A server therefore counts **only one GPU's manufacturing impact**, regardless of whether
   4 or 8 are configured — verified: 1 vs 8 GPUs give an identical server embedded value.
   *Fix:* we add the missing `(units − 1)` GPUs using the per-GPU embedded value from the
   `/v1/component/gpu` endpoint.
2. **GPU power absent from the use phase.** The server's `avg_power` is modelled from
   **CPU + RAM only** (`DeviceServer.model_power_consumption`); GPU `avg_power` is `null`.
   So GPU electricity — the dominant load on these nodes — is not counted at all.
   *Fix:* we add GPU use energy = `units × TDP × load_factor × duration × elec_factor`,
   computed via `/v1/component/gpu` (which *does* scale use by units).

Both corrections reuse BoaviztAPI's own GPU component endpoint, so no impact factors are
hardcoded — only the inputs below.

- **GPU TDP:** A100 SXM4 = 400 W, A100 PCIe = 300 W, RTX A6000 = 300 W.
- **GPU load factor:** **50%** average utilisation (`GPU_LOAD_FACTOR`, tunable). Real Mila
  GPU utilisation is likely higher; raising this scales the GPU use phase linearly.

## Electricity grid (Quebec)

BoaviztAPI has **no Quebec sub-region**. Rather than use the Canadian national average
(~120 gCO₂/kWh, which is ~3× too high for Quebec's ~99% hydro grid), we **override the GWP
electricity factor** to **38 gCO₂eq/kWh** (`usage.elec_factors.gwp = 0.038`), the live
Quebec carbon intensity reported by
[Electricity Maps (CA-QC)](https://app.electricitymaps.com/map/zone/CA-QC/live/fifteen_minutes).

`usage_location` is still set to `CAN`, so the **ADP and PE** use-phase factors keep the
Canada grid values (Electricity Maps only publishes carbon intensity, and these criteria
are far less location-sensitive)

## Results (Quebec GWP grid = 38 gCO₂eq/kWh, ADP/PE = Canada)

Per-unit and fleet totals (37 servers) for the three default criteria — GWP (climate
change), ADP (abiotic resource depletion), PE (primary energy). Values include the GPU
embedded + GPU use corrections above.

| Model | Qty | GPUs | GWP/unit (kgCO₂eq) | embedded / use |
|---|---|---|---|---|
| Dell XE8545 (4 GPU) | 29 | 4× A100 80 GB | 7,090 | 4,925 / 2,165 |
| Dell R6525 (4 GPU) | 4 | 4× A100 40 GB | 4,735 | 2,880 / 1,855 |
| Gigabyte G292-Z40 (4 GPU) | 1 | 4× A100 80 GB | 6,724 | 4,825 / 1,899 |
| Gigabyte G292-Z40 (8 GPU) | 1 | 8× A6000 48 GB | 8,198 | 5,500 / 2,698 |
| NVIDIA DGX A100 640 GB (8 GPU) | 2 | 8× A100 80 GB | 13,360 | 9,626 / 3,730 |

**Fleet total (37 servers):**

| Criterion | Total | Embedded | Use |
|---|---|---|---|
| GWP (kgCO₂eq) | ~266,200 | ~183,900 | ~82,300 |
| ADP (kgSbeq) | ~8.74 | ~8.53 | ~0.22 |
| PE (MJ) | ~28,500,000 | ~2,453,000 | ~26,058,000 |

> On Quebec's low-carbon grid, **embedded (manufacturing) impact dominates GWP (~69%)**.
> Primary energy is instead use-dominated (PE keeps the higher Canada grid factor and GPU
> electricity is large). The GPU use phase scales linearly with `GPU_LOAD_FACTOR` (50% here).
