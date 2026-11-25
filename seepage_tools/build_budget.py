"""Build an offline transient seepage budget viewer from an XSLOPE 1.0.9 workbook."""
import argparse
import base64
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import os

import numpy as np
from xslope import seep
from xslope.fileio import load_slope_data


def recorded_solver(record):
    # Capture the solver's assembled nodal storage increment and its actual reaction.
    # The observation point is after the adaptive time-error rejection branch.
    source = inspect.getsource(seep.run_transient_seepage)
    anchors = [
        ("(inflow, rate, flux, dir_mask, bt == 2), cycling",
         "(inflow, rate, flux, dir_mask, bt == 2, R_new), cycling"),
        ("            dt_history.append(dt_try)",
         "            record(t, dt_try, *flow[1:4], flow[5])\n            dt_history.append(dt_try)"),
    ]
    for old, new in anchors:
        if source.count(old) != 1:
            raise RuntimeError("Unsupported XSLOPE solver source. This recorder targets release 1.0.9.")
        source = source.replace(old, new)
    namespace = dict(vars(seep), record=record)
    exec(compile(source, "<XSLOPE transient budget observer>", "exec"), namespace)
    return namespace["run_transient_seepage"]


def encode(array):
    raw = np.asarray(array, dtype="<f8", order="C").tobytes()
    return base64.b64encode(gzip.compress(raw, compresslevel=4, mtime=0)).decode("ascii")


def mesh_edges(elements, types):
    edges = {}
    for nodes, kind in zip(elements, types):
        corners = {3:3, 6:3, 4:4, 8:4, 9:4}[int(kind)]
        for j in range(corners):
            a,b = sorted((int(nodes[j]), int(nodes[(j+1)%corners])))
            mid = int(nodes[corners+j]) if kind > corners else -1
            if (a,b) not in edges:
                edges[a,b] = [a,b,mid,1]
            else:
                edges[a,b][3] += 1
    return list(edges.values())


def section_triangles(elements, types):
    """Subdivide supported elements for nodal contour interpolation."""
    patterns = {
        3: [(0, 1, 2)],
        6: [(0, 3, 5), (3, 1, 4), (5, 4, 2), (3, 4, 5)],
        4: [(0, 1, 2), (0, 2, 3)],
        8: [(0, 4, 7), (1, 5, 4), (2, 6, 5), (3, 7, 6), (4, 5, 6), (4, 6, 7)],
        9: [(a, b, 8) for a, b in zip((0,4,1,5,2,6,3,7), (4,1,5,2,6,3,7,0))],
    }
    return [[int(row[i]) for i in tri] for row, kind in zip(elements, types, strict=True)
            for tri in patterns[int(kind)]]


def material_map(sd, model):
    """Match each element perimeter with its one-based solver material ID."""
    cells = []
    for row, kind, mid in zip(sd["elements"], sd["element_types"], sd["element_materials"], strict=True):
        corners = {3: 3, 6: 3, 4: 4, 8: 4, 9: 4}[int(kind)]
        perimeter = []
        for i in range(corners):
            perimeter.append(int(row[i]))
            if kind > corners:
                perimeter.append(int(row[corners + i]))
        cells.append([int(mid), perimeter])
    materials = {}
    for mid in sorted({cell[0] for cell in cells}):
        if not 1 <= mid <= len(model["materials"]):
            raise ValueError(f"Invalid mesh material ID {mid}")
        i = mid - 1
        row = {"name": sd["material_names"][i] or f"Material {mid}"}
        for key in ("k1", "k2", "angle", "ss", "sy"):
            value = float(sd[key + "_by_mat"][i])
            missing = key in ("ss", "sy") and model["materials"][i].get({"ss":"Ss", "sy":"Sy"}[key]) is None
            row[key] = value if np.isfinite(value) and not missing else None
        materials[mid] = row
    return {"cells": cells, "materials": materials}


def frame_velocities(seep_data, frames, unconfined):
    """Use the same velocity recovery as Studio's saved transient frames."""
    elements, element_types, k1, k2, angle, kr0, h0, vg_a, vg_n, vg_l, model = seep._elem_props(seep_data)
    options = {"element_types": element_types}
    if unconfined:
        options.update(kr0=kr0, h0=h0, vg_a=vg_a, vg_n=vg_n, vg_l=vg_l, model=model)
    return [seep.compute_velocity(seep_data["nodes"], elements, frame["head"], k1, k2, angle, **options)
            for frame in frames]


def build(path, output, mode="auto", bc_set=1, climate=None):
    model = load_slope_data(str(path))
    if climate is not None:
        climate.set_materials(model)
    mesh = model.get("mesh")
    if mesh is None:
        raise ValueError("Build and save the mesh in XSLOPE Studio first. Keep the matching _mesh.json beside the workbook.")
    schedule = seep.build_tseep_data(model)
    transient = bool(schedule) if mode == "auto" else mode == "transient"
    if transient and not schedule:
        raise ValueError("This workbook has no transient schedule.")
    if transient and bc_set != 1:
        raise ValueError("Transient seepage uses BC set 1.")
    sd = seep.build_seep_data(mesh, model, seep_bc=bc_set)
    nodes = np.asarray(sd['nodes'])
    features = []
    bc = model['seepage_bc' if bc_set == 1 else 'seepage_bc2']
    if not bc:
        raise ValueError(f'Boundary condition set {bc_set} is empty.')
    for category, entries in [('Head',bc['specified_heads']), ('Flux',bc['specified_fluxes']),
                              ('Exit face',[dict(coords=bc['exit_face'])] if bc['exit_face'] else [])]:
        for idx,block in enumerate(entries,1):
            coords = np.array(block['coords'])
            if len(coords)<2:
                continue
            val = block.get('head',block.get('flux',''))
            label = f"{category} {idx} · {val}".rstrip(' ·')
            mask=seep._min_distance_to_polyline(nodes,coords)<=seep.bc_node_tolerance(nodes)
            features.append([label,np.flatnonzero(mask).tolist()])
    edges = mesh_edges(mesh["elements"], mesh["element_types"])
    if climate is not None:
        climate.bind(sd,schedule,edges,features,transient)
    times, dt, external, transfer, storage = [], [], [], [], []
    def record(t, step, rate, flux, fixed, delta):
        ext = np.where(fixed,rate,flux)
        times.append(float(t)); dt.append(float(step))
        external.append(ext)
        storage.append(delta/step)
        transfer.append(delta/step-rate)  # Net conductive inflow from other nodal rows.
    if transient:
        result=recorded_solver(record)(sd,schedule)
        if not result['converged'] or result['cancelled'] or not np.isclose(times[-1], schedule['duration']):
            raise ValueError("The transient simulation did not complete with convergence.")
        external=np.asarray(external); storage=np.asarray(storage); transfer=np.asarray(transfer)
        for term, key in [(external,'cumulative_inflow'),(storage,'final_stored_change')]:
            actual=np.dot(term.sum(axis=1),dt)
            np.testing.assert_allclose(actual,result['mass_balance'][key],rtol=1e-7,atol=1e-8)
        np.testing.assert_allclose(transfer.sum(axis=1),0,atol=1e-7)
        for frame in result['frames'][1:]:
            i=np.argmin(np.abs(np.asarray(times)-frame['time']))
            for a,b in [(external[i].clip(min=0).sum(),frame['inflow']),
                        (-external[i].clip(max=0).sum(),frame['outflow'])]:
                np.testing.assert_allclose(a,b,rtol=1e-7,atol=1e-9)
    else:
        steady = seep.run_seepage_analysis(sd)
        if not steady["converged"]:
            raise ValueError("The steady seepage solve did not converge.")
        kinds = sd["bc_type"]
        if schedule and bc_set == 1:
            kinds = seep.resolve_transient_bc(sd, schedule, 0)[0]
        q = np.asarray(steady["q"])
        # Inactive exit rows solve the free equation, so q there equals
        # the prescribed nodal flux (or zero to solver precision).
        ext = np.where(kinds != 0, q, steady["flux_nodal"])
        np.testing.assert_allclose(np.maximum(ext, 0).sum(), steady["flowrate"],
                                   rtol=1e-7, atol=1e-8)
        times, dt = [0.0], [0.0]
        external, transfer, storage = [ext], [-q], [np.zeros_like(q)]
        result = {"times": [0.0], "frames": [{"head": steady["head"]}]}
    velocities = (frame_velocities(sd, result["frames"], result["unconfined"])
                  if transient else [steady["velocity"]])
    data = dict(geology=material_map(sd, model), model=path.stem, analysis="transient" if transient else "steady", bcSet=bc_set, nodes=nodes.tolist(), edges=mesh_edges(mesh['elements'],mesh['element_types']),
                triangles=section_triangles(mesh['elements'], mesh['element_types']),
                features=features, times=times,dt=dt, frames=result['times'],
                external=encode(external), transfer=encode(transfer), storage=encode(storage),
                head=encode([f['head'] for f in result['frames']]), velocity=encode(velocities), gamma=sd['unit_weight'],
                timeUnit=sd.get('time_unit') or 'time unit',system=sd.get('unit_system') or '',
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if climate is not None:
        data["climate"] = climate.results(times,dt,external,transient)
    layout=Path(__file__).with_name('viewer.html').read_text(encoding='utf-8')
    html=layout.replace('/*MODEL_DATA*/',json.dumps(data,separators=(',',':')).replace('<','\\u003c'))
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=output.parent,delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write(html)
        except BaseException:
            f.close();tmp.unlink(missing_ok=True);raise
    try:
        os.replace(tmp,output)
    finally:
        tmp.unlink(missing_ok=True)
    print(f"{len(times)} accepted steps, {len(result['times'])} saved frames. Wrote {output}")


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--mode',choices=('auto','steady','transient'),default='auto')
    parser.add_argument('--bc-set',type=int,choices=(1,2),default=1)
    args=parser.parse_args()
    build(args.workbook,args.output or args.workbook.with_name(args.workbook.stem+'_budget.html'),args.mode,args.bc_set)
