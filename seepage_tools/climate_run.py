"""Run an England-wide daily climate scenario against an existing XSLOPE section."""
import argparse
import datetime
import getpass
import html
import json
import os
import tempfile
from pathlib import Path
from climate_data import date360,load_climate,ordinal
from surface_water import ClimateCase,write_csv


def template(workbook,bc_set=1):
    from xslope.fileio import load_slope_data
    from xslope import seep
    model=load_slope_data(str(workbook));mesh=model['mesh']
    if mesh is None:raise ValueError('Save the matching mesh beside the workbook first.')
    bc=model['seepage_bc' if bc_set==1 else 'seepage_bc2']
    heads=[f"Head {i} · {v['head']}" for i,v in enumerate(bc['specified_heads'],1)]
    pits=[dict(name=str(v['head']),boundary_labels=[label],section_length_m=None,plan_area_m2=None,
               exposed_area_fraction=[[0,1]]) for label,v in zip(heads,bc['specified_heads']) if v.get('kind')=='reservoir']
    schedule=seep.build_tseep_data(model)
    return dict(site_name=workbook.stem,workbook=str(workbook.resolve()),site_bng_m=[None,None],
                analysis='transient' if schedule else 'steady',bc_set=bc_set,
                start_dates=['2030-10-01'],duration_days=schedule['duration'] if schedule else 189,
                spinup_days=360,members=['01'],paired_csv='',rain_folder='',pet_folder='PET',
                zones=[dict(name='Intact ground',section_x_ranges_m=[[float(min(mesh['nodes'][:,0])),float(max(mesh['nodes'][:,0]))]],
                            capacity_mm=100,initial_fraction=0.5,infiltration_limit_mm_day=25,et_factor=1)],
                pits=pits,conductivity_cases=[dict(name='Baseline',K_multiplier={})],
                available_head_labels=heads,available_material_names=[v['name'] for v in model['materials']])


def run(project,key=None):
    from build_budget import build
    cfg=json.loads(project.read_text(encoding='utf-8-sig'));base=project.parent
    days=int(cfg['duration_days']); spin=int(cfg['spinup_days'])
    if days!=cfg['duration_days'] or days<1 or spin!=cfg['spinup_days'] or spin<0:raise ValueError('Duration and spin-up must be whole days.')
    dates=[ordinal(d) for d in cfg['start_dates']]
    if not dates or len(set(dates))!=len(dates):raise ValueError('Enter distinct construction start dates.')
    mode=cfg['analysis'];bc_set=cfg['bc_set']
    if mode not in ('steady','transient') or bc_set not in (1,2) or mode=='transient' and bc_set!=1:raise ValueError('Use transient BC1 or steady BC1/BC2.')
    if not cfg['zones'] or not cfg['conductivity_cases']:raise ValueError('Enter at least one recharge zone and K case.')
    for name in ('zones','pits','conductivity_cases'):
        labels=[v['name'] for v in cfg[name]]
        if any(not s for s in labels) or len(labels)!=len(set(labels)):raise ValueError(f'Use unique names in {name}.')
    workbook=base/cfg['workbook']
    print('Reading paired climate inputs…',flush=True)
    rain,pet,sources=load_climate(cfg,base,key,min(dates)-spin,max(dates)+days-1)
    root=Path(tempfile.mkdtemp(prefix='XSLOPE_climate_'+datetime.date.today().isoformat()+'_',dir=base))
    print('Output folder:',root,flush=True)
    (root/'project.json').write_text(json.dumps(cfg,indent=2))
    (root/'climate_sources.json').write_text(json.dumps(sources,indent=2))
    summaries=[];links=[];total=len(dates)*len(rain)*len(cfg['conductivity_cases'])
    for start in dates:
        for m in rain:
            for j,case in enumerate(cfg['conductivity_cases'],1):
                folder=root/f'{date360(start)}_member{m}_K{j}';folder.mkdir()
                c=ClimateCase(cfg,m,start,rain[m],pet[m],folder,case)
                print(f'Running {len(links)+1}/{total}: {c.name}',flush=True)
                build(workbook,folder/'results.html',mode,bc_set,climate=c)
                links.append((folder.name,c.name))
                summaries.extend([[date360(start),m,case['name'],*row] for row in c.summary])
                write_csv(root/'scenario_summary.csv',['start','member','conductivity_case','pit','peak_daily_mean_m3_day','period_volume_m3','groundwater_m3','direct_rain_m3'],summaries)
                table=''.join('<tr>'+''.join('<td>'+html.escape(str(round(v,3) if isinstance(v,float) else v))+'</td>' for v in row)+'</tr>' for row in summaries)
                nav=''.join(f'<li><a href="{f}/results.html">{html.escape(label)}</a></li>' for f,label in links)
                (root/'index.html').write_text(f'<!doctype html><meta charset=utf-8><style>body{{font:16px system-ui;margin:3%}}td,th{{padding:.4em;text-align:left}}tr:nth-child(even){{background:#edf4f3}}</style><h1>Climate scenarios</h1><p>{len(links)} of {total} completed · {mode}</p><ul>{nav}</ul><table><tr><th>Start<th>Member<th>K case<th>Pit<th>Peak daily mean (m³/day)<th>Volume (m³)<th>Groundwater (m³)<th>Direct rain (m³)</tr>{table}</table>',encoding='utf-8')
    return root/'index.html'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path);args=parser.parse_args()
    cfg=json.loads(args.project.read_text(encoding='utf-8-sig'))
    key=os.getenv('UKCP_API_KEY','')
    if not key and not cfg.get('paired_csv') and not cfg.get('rain_folder'):
        key=getpass.getpass('UKCP API key: ')
    print(run(args.project,key))
