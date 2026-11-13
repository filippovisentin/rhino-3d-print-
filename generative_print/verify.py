"""Verifica indipendente di un file STL, fuori da Rhino.

Uso:
    python -m generative_print.verify runs/demo/iter_01            (cartella con model.stl e params.json)
    python -m generative_print.verify modello.stl --params p.json --out metrics.json
"""
import argparse
import json
import os
import sys

from . import checks, generators, mesh_io


def verify_file(stl_path, params=None, samples=3000):
    V, F = mesh_io.read_stl(stl_path)
    p = generators.resolve(params)
    skin = None
    skin_path = os.path.join(os.path.dirname(stl_path), "skin.npy")
    if p.get("object") == "vase":
        import numpy as np
        skin = np.load(skin_path) if os.path.exists(skin_path) else generators.vessel_skin(p)
    result = checks.run_checks(V, F, p, samples=samples, skin=skin)
    result["file"] = os.path.basename(stl_path)
    result["triangles"] = int(len(F))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verifica di stampabilita FDM di un STL")
    ap.add_argument("target", help="file STL oppure cartella di iterazione")
    ap.add_argument("--params", help="JSON dei parametri (vincoli e dati di stampa)")
    ap.add_argument("--out", help="dove scrivere metrics.json")
    ap.add_argument("--samples", type=int, default=3000)
    a = ap.parse_args(argv)
    stl, ppath, out = a.target, a.params, a.out
    if os.path.isdir(a.target):
        stl = os.path.join(a.target, "model.stl")
        ppath = ppath or os.path.join(a.target, "params.json")
        out = out or os.path.join(a.target, "metrics.json")
    params = None
    if ppath and os.path.exists(ppath):
        with open(ppath, encoding="utf-8") as fh:
            params = json.load(fh)
    result = verify_file(stl, params, a.samples)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    print(checks.format_report(result))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
