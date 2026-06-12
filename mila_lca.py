"""LCA of the Mila cluster (Montreal, Quebec) via a local BoaviztAPI instance.

Each line of the inventory is enriched with CPU / RAM / disk / GPU details
cross-referenced from the Mila node profile table:
https://docs.mila.quebec/technical_reference/clusters/mila/nodes/

Two BoaviztAPI limitations are corrected here (see mila_lca_methodology.md):
  1. EMBEDDED: the server model counts only ONE GPU regardless of `units`
     (`gpu_impact_embedded` never multiplies by units). We add the missing
     (units - 1) GPUs.
  2. USE: GPU electricity is not modelled at all (server avg_power is built from
     CPU+RAM only; GPU avg_power is null). We add GPU use energy explicitly.

Both corrections reuse BoaviztAPI's own GPU component endpoint, so we never
hardcode impact factors - only GPU TDP and an average load factor.

Usage location is Canada ("CAN") for ADP/PE; GWP electricity is overridden to
Quebec's grid intensity.
"""
import json
import urllib.request
import urllib.error

BASE = "http://localhost:5000"
LOCATION = "CAN"          # Canada: used for ADP/PE electricity factors (no Quebec sub-region)
QC_GWP_FACTOR = 0.038     # kgCO2eq/kWh - Quebec grid (Electricity Maps CA-QC)
GPU_LOAD_FACTOR = 0.5     # average GPU utilisation for the use phase (tunable)
DURATION = 35040          # hours (~4 yr) = full lifetime -> allocation = 1
CRITERIA = ("gwp", "adp", "pe")

# (label, quantity, mila_node, archetype_hint, gpu_tdp_W, server_config)
FLEET = [
    (
        "Dell XE8545 (4 GPU)", 29, "cn-g[001-029]", "platfom_gpucompute_high", 400,
        {"model": {"type": "rack"},
         "configuration": {
             "cpu": {"units": 2, "name": "AMD EPYC 7543"},           # 2x32c = 64 cores
             "ram": [{"units": 16, "capacity": 64}],                  # 1024 GB
             "disk": [{"units": 2, "type": "ssd", "capacity": 3840}], # ~7 TB scratch
             "gpu": {"units": 4, "name": "NVIDIA A100 SXM4 40GB", "vram": 80}}},
    ),
    (
        "Dell R6525 (4 GPU)", 4, "cn-k[001-004]", "platfom_gpucompute_high", 400,
        {"model": {"type": "rack"},
         "configuration": {
             "cpu": {"units": 2, "name": "AMD EPYC 7443"},           # 2x24c = 48 cores
             "ram": [{"units": 16, "capacity": 32}],                  # 512 GB
             "disk": [{"units": 2, "type": "ssd", "capacity": 1920}], # ~3.6 TB scratch
             "gpu": {"units": 4, "name": "NVIDIA A100 SXM4 40GB", "vram": 40}}},
    ),
    (
        "Gigabyte G292-Z40 (4 GPU)", 1, "cn-i001", "platfom_gpucompute_high", 300,
        {"model": {"type": "rack"},
         "configuration": {
             "cpu": {"units": 2, "name": "AMD EPYC 7543"},           # 2x32c = 64 cores
             "ram": [{"units": 16, "capacity": 64}],                  # 1024 GB
             "disk": [{"units": 2, "type": "ssd", "capacity": 1920}], # ~3.6 TB scratch
             "gpu": {"units": 4, "name": "NVIDIA A100 PCIe 40GB", "vram": 80}}},
    ),
    (
        "Gigabyte G292-Z40 (8 GPU)", 1, "cn-j001", "platfom_gpucompute_high", 300,
        {"model": {"type": "rack"},
         "configuration": {
             "cpu": {"units": 2, "name": "AMD EPYC 7543"},           # 2x32c = 64 cores
             "ram": [{"units": 16, "capacity": 64}],                  # 1024 GB
             "disk": [{"units": 2, "type": "ssd", "capacity": 1920}], # ~3.6 TB scratch
             # A6000 absent from DB -> use A4500 (same GA102 die) as proxy, vram 48
             "gpu": {"units": 8, "name": "NVIDIA RTX A4500", "vram": 48}}},
    ),
    (
        "NVIDIA DGX A100 640GB (8 GPU)", 2, "cn-d[003-004]", "platfom_gpucompute_veryhigh", 400,
        {"model": {"type": "rack"},
         "configuration": {
             "cpu": {"units": 2, "name": "AMD EPYC 7742"},           # 2x64c = 128 cores
             "ram": [{"units": 32, "capacity": 64}],                  # 2048 GB
             "disk": [{"units": 2, "type": "ssd", "capacity": 1920},
                      {"units": 4, "type": "ssd", "capacity": 3840}], # ~28 TB raw
             "gpu": {"units": 8, "name": "NVIDIA A100 SXM4 40GB", "vram": 80}}},
    ),
]


def _post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API error {e.code} on {path}: {e.read().decode()}")


def _ev(impacts, crit, phase):
    """embedded/use value for a criterion, 0.0 if missing/not-implemented."""
    v = impacts.get(crit, {}).get(phase, {})
    return v.get("value", 0.0) if isinstance(v, dict) and isinstance(v.get("value"), (int, float)) else 0.0


def server_impacts(config, archetype):
    body = json.loads(json.dumps(config))
    body["usage"] = {"usage_location": LOCATION, "elec_factors": {"gwp": QC_GWP_FACTOR}}
    return _post(f"/v1/server/?verbose=false&archetype={archetype}&duration={DURATION}", body)["impacts"]


def gpu_component_impacts(gpu_cfg, tdp):
    """Per-GPU embedded (units ignored by API) + use scaled to N GPUs."""
    body = dict(gpu_cfg)
    body["usage"] = {"usage_location": LOCATION, "use_time_ratio": 1,
                     "avg_power": tdp * GPU_LOAD_FACTOR, "elec_factors": {"gwp": QC_GWP_FACTOR}}
    return _post(f"/v1/component/gpu?verbose=false&duration={DURATION}", body)["impacts"]


def main():
    units_str = {"gwp": "kgCO2eq", "adp": "kgSbeq", "pe": "MJ"}
    totals = {c: {"embedded": 0.0, "use": 0.0} for c in CRITERIA}
    n_servers = 0

    print("=" * 88)
    print(f"Mila cluster LCA  |  GWP grid={QC_GWP_FACTOR*1000:.0f} gCO2eq/kWh (Quebec)  "
          f"|  ADP/PE grid=CAN  |  GPU load={GPU_LOAD_FACTOR:.0%}")
    print("  (corrected for BoaviztAPI GPU embedded-units bug + missing GPU use power)")
    print("=" * 88)

    for label, qty, node, archetype, tdp, config in FLEET:
        n_servers += qty
        s = server_impacts(config, archetype)
        gpu_cfg = config["configuration"].get("gpu")
        n_gpu = gpu_cfg["units"] if gpu_cfg else 0
        g = gpu_component_impacts({k: v for k, v in gpu_cfg.items() if k != "usage"}, tdp) if gpu_cfg else {}

        print(f"\n{label}  x{qty}   [{node}]   GPUs/server={n_gpu} @ {tdp} W")
        for c in CRITERIA:
            base_emb, base_use = _ev(s, c, "embedded"), _ev(s, c, "use")
            per_gpu_emb = _ev(g, c, "embedded") if g else 0.0   # API reports 1 GPU
            gpu_use = _ev(g, c, "use") if g else 0.0            # already x n_gpu
            extra_emb = (n_gpu - 1) * per_gpu_emb if n_gpu else 0.0
            emb = base_emb + extra_emb
            use = base_use + gpu_use
            tot = emb + use
            print(f"  {c:4} /unit: embedded={emb:.4g} (+{extra_emb:.4g} gpu)  "
                  f"use={use:.4g} (+{gpu_use:.4g} gpu)  total={tot:.4g} {units_str[c]}")
            totals[c]["embedded"] += emb * qty
            totals[c]["use"] += use * qty

    print("\n" + "=" * 88)
    print(f"FLEET TOTAL ({n_servers} servers)")
    for c in CRITERIA:
        emb, use = totals[c]["embedded"], totals[c]["use"]
        print(f"  {c:4}: total={emb+use:.6g} {units_str[c]}  (embedded={emb:.6g}, use={use:.6g})")
    print("=" * 88)


if __name__ == "__main__":
    main()
