"""Lettura e scrittura di mesh: STL binario/ASCII e 3MF minimale. Unita: mm."""
import struct
import zipfile

import numpy as np


def write_stl(path, V, F, name="generative-print"):
    V = np.asarray(V, dtype=np.float32)
    F = np.asarray(F)
    tri = V[F]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.maximum(ln, 1e-20)
    rec = np.zeros(len(F), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"] = n
    rec["v"] = tri
    with open(path, "wb") as fh:
        fh.write(name.encode("ascii", "replace")[:80].ljust(80, b" "))
        fh.write(struct.pack("<I", len(F)))
        fh.write(rec.tobytes())


def read_stl(path, weld_decimals=4):
    """Legge un STL e salda i vertici coincidenti. Restituisce (V, F)."""
    with open(path, "rb") as fh:
        data = fh.read()
    ntri = struct.unpack("<I", data[80:84])[0] if len(data) >= 84 else -1
    if len(data) == 84 + ntri * 50:
        rec = np.frombuffer(data, offset=84, count=ntri,
                            dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
        tri = rec["v"].astype(np.float64)
    else:
        pts = [ln.split()[1:4] for ln in data.decode("ascii", "replace").splitlines()
               if ln.strip().startswith("vertex")]
        tri = np.array(pts, dtype=np.float64).reshape(-1, 3, 3)
    flat = tri.reshape(-1, 3)
    key = np.round(flat, weld_decimals)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    F = np.asarray(inv).reshape(-1, 3).astype(np.int64)
    ok = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])
    return uniq, F[ok]


def write_3mf(path, V, F, name="generative-print"):
    V = np.asarray(V, dtype=float)
    F = np.asarray(F)
    vs = "".join('<vertex x="%.5f" y="%.5f" z="%.5f"/>' % tuple(v) for v in V)
    ts = "".join('<triangle v1="%d" v2="%d" v3="%d"/>' % tuple(f) for f in F)
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Title">%s</metadata>'
             '<resources><object id="1" type="model"><mesh><vertices>%s</vertices>'
             '<triangles>%s</triangles></mesh></object></resources>'
             '<build><item objectid="1"/></build></model>') % (name, vs, ts)
    types = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
