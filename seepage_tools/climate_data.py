"""Daily UKCP18 Regional rainfall and UKCEH Hydro-PE for a BNG site."""
import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse as url
import urllib.request as http
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HOST = 'ukclimateprojections-ui.metoffice.gov.uk'
PRODUCT = 'LS3_Subset_02'
PPE = ('01','04','05','06','07','08','09','10','11','12','13','15')


def ordinal(date):
    y,m,d = map(int,str(date).split('-'))
    if y<1 or not 1<=m<=12 or not 1<=d<=30:
        raise ValueError(f'Invalid UKCP18 360-day date: {date}')
    return y*360+(m-1)*30+d-1


def date360(i):
    y,r=divmod(i,360)
    return f'{y:04d}-{r//30+1:02d}-{r%30+1:02d}'


def member(value):
    import re
    match=re.fullmatch(r'(?:r)?(\d{1,2})(?:i1p\d+(?:f\d+)?)?',str(value).strip())
    if not match: raise ValueError(f'Unrecognised ensemble member {value!r}')
    return match[1].zfill(2)


def validate_url(link):
    u=url.urlsplit(link)
    if u.scheme!='https' or u.hostname!=HOST or u.port not in (443,None) or u.username:
        raise ValueError('UKCP returned a URL outside its HTTPS service. Use a locally downloaded NetCDF.')
    return u


class SameHostRedirect(http.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch_rain(cache,e,n,first,last,key,log=print):
    """Cache a complete site request, including ensemble members, independent of the key."""
    if not key: raise ValueError('Enter the Met Office UKCP API key or select local rainfall files.')
    first=max(1980,min(first,2070)); last=min(2080,max(last,first+10))
    inputs=f'TemporalAverage=day;Area=point|{e}|{n};Collection=land-rcm;ClimateChangeType=absolute;EnsembleMemberSet=land-rcm;DataFormat=netcdf;TimeSlice={first}|{last};Variable=pr'
    root=Path(cache)/hashlib.sha256(inputs.encode()).hexdigest()[:20]
    root.mkdir(parents=True,exist_ok=True)
    manifest=root/'files.json'
    if manifest.exists():
        files=[root/f for f in json.loads(manifest.read_text())]
        if files and all(p.is_file() for p in files):return files
    opener=http.build_opener(SameHostRedirect)
    def get(link):
        u=validate_url(link)
        query=[(k,v) for k,v in url.parse_qsl(u.query) if k.lower()!='apikey']
        target=url.urlunsplit(u._replace(query=url.urlencode(query+[('ApiKey',key)])))
        try:return opener.open(target,timeout=120)
        except (OSError,ValueError) as exc:
            raise RuntimeError(f'UKCP request failed ({getattr(exc,"code","connection error")}). Check key and connection.') from None
    def xml(link):
        with get(link) as response:return ET.parse(response).getroot()
    status_file=root/'pending.json'
    if status_file.exists():
        response=xml(json.loads(status_file.read_text()))
    else:
        args=dict(service='WPS',request='Execute',version='1.0.0',Identifier=PRODUCT,Format='text/xml',Inform='false',Store='true',Status='true',DataInputs=inputs)
        response=xml('https://'+HOST+'/wps?'+url.urlencode(args))
    while True:
        tags={el.tag.split('}')[-1] for el in response.iter()}
        if tags & {'ProcessFailed','ExceptionReport','Exception'}:
            status_file.unlink(missing_ok=True)
            raise RuntimeError('The UKCP service rejected this job. Check My Jobs on the UKCP website for the provider message.')
        if 'ProcessSucceeded' in tags:break
        location=response.get('statusLocation') or (json.loads(status_file.read_text()) if status_file.exists() else '')
        u=validate_url(location)
        clean=url.urlunsplit(u._replace(query=url.urlencode([(k,v) for k,v in url.parse_qsl(u.query) if k.lower()!='apikey'])))
        status_file.write_text(json.dumps(clean))
        log('Waiting for UKCP rainfall. Re-running with the same settings resumes the job.',flush=True)
        time.sleep(30);response=xml(clean)
    links=[(el.text or '').strip() for el in response.iter() if el.tag.split('}')[-1]=='FileURL']
    nc=[l for l in links if url.urlsplit(l).path.lower().endswith('.nc')]
    sources=nc or [l for l in links if url.urlsplit(l).path.lower().endswith('.zip')]
    if not sources:raise ValueError('UKCP returned no NetCDF or ZIP file.')
    names=[]
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        for i,l in enumerate(dict.fromkeys(sources)):
            p=Path(tmp)/str(i)
            with get(l) as stream,p.open('wb') as out:shutil.copyfileobj(stream,out)
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as z:
                    for j,item in enumerate(z.infolist()):
                        if item.filename.lower().endswith('.nc'):
                            name=f'{i}-{j}-'+Path(item.filename).name
                            with z.open(item) as src,(root/name).open('wb') as dest:shutil.copyfileobj(src,dest)
                            names.append(name)
            else:
                name=f'{i}-'+Path(url.urlsplit(l).path).name
                p.replace(root/name);names.append(name)
    if not names:raise ValueError('UKCP archive contains no NetCDF.')
    manifest.write_text(json.dumps(names));status_file.unlink(missing_ok=True)
    return [root/f for f in names]


def nc_series(files,field,e,n,wanted,first,last):
    import numpy as np
    try:from netCDF4 import Dataset,num2date
    except ImportError:raise ValueError('Run Install_Climate_Reader.cmd once for NetCDF support.') from None
    data={m:{} for m in wanted};sources=[]
    for file,assigned in files:
        with Dataset(str(file)) as ds:
            accepted=('pr','precipitation_flux','lwe_precipitation_rate','precipitation_amount') if field=='rain' else ('pet','potential_evapotranspiration')
            candidates=[v for name,v in ds.variables.items() if name.lower() in accepted or getattr(v,'standard_name','') in accepted and name.lower()!='peti']
            if len(candidates)!=1:raise ValueError(f'{file.name}: expected one {field} variable (use PET, excluding PETI).')
            v=candidates[0];axes={};index=[];ens=None;times=None;cell={}
            for dim in v.dimensions:
                coord=ds.variables.get(dim)
                std=getattr(coord,'standard_name','');diml=dim.lower()
                if diml=='time' or std=='time':
                    if getattr(coord,'calendar','') not in ('360_day','360'):raise ValueError('Expected UKCP18 360_day calendar.')
                    days=num2date(coord[:],coord.units,calendar='360_day')
                    ords=np.array([ordinal(f'{d.year}-{d.month}-{d.day}') for d in days])
                    ix=np.flatnonzero((ords>=first)&(ords<=last))
                    if ix.size and not np.all(np.diff(ix)==1):raise ValueError('Non-contiguous time coordinate')
                    times=ords[ix];index.append(slice(ix[0],ix[-1]+1) if ix.size else slice(0,0))
                elif std in ('projection_x_coordinate','projection_y_coordinate') or diml in ('x','y','easting','northing','projection_x_coordinate','projection_y_coordinate'):
                    xaxis=std=='projection_x_coordinate' or diml in ('x','easting','projection_x_coordinate')
                    target=e if xaxis else n
                    units=str(getattr(coord,'units','')).lower()
                    if units not in ('km','m','metres','meters','metre','meter'):raise ValueError('Expected projected BNG coordinates in metres or kilometres.')
                    axis=np.asarray(coord[:])*(1000 if units=='km' else 1)
                    k=int(np.argmin(abs(axis-target)))
                    if abs(axis[k]-target)>6000.1:raise ValueError(f'{file.name}: site outside the selected 12 km cell/grid.')
                    cell['E' if xaxis else 'N']=float(axis[k]);index.append(k)
                elif 'ensemble' in diml or 'realiz' in diml or std=='realization':
                    ens=len(index);axes['members']=[member(x) for x in coord[:]];index.append(0)
                elif len(ds.dimensions[dim])==1:index.append(0)
                else:raise ValueError(f'Unsupported dimension {dim} in {file.name}')
            if times is None:raise ValueError('Missing time coordinate')
            if not len(times):continue
            for axis,std,target in [('E','projection_x_coordinate',e),('N','projection_y_coordinate',n)]:
                if axis not in cell:
                    cv=next((c for c in ds.variables.values() if getattr(c,'standard_name','')==std and c.size==1),None)
                    if cv is None:raise ValueError(f'{file.name}: missing BNG {axis} coordinate.')
                    cell[axis]=float(np.asarray(cv[:]).item())*(1000 if cv.units=='km' else 1)
                    if abs(cell[axis]-target)>6000.1:raise ValueError(f'{file.name}: wrong site.')
            members=axes.get('members')
            if members is None:
                cv=next((c for k,c in ds.variables.items() if (k in ('ensemble_member','realization') or getattr(c,'standard_name','')=='realization') and c.size==1),None)
                value=np.asarray(cv[:]).item() if cv is not None else getattr(ds,'ensemble_member',assigned)
                if value is None:raise ValueError(f'{file.name}: place single-member files in a member folder such as 01.')
                members=[member(value)]
            unit=str(getattr(v,'units','')).lower().replace(' ','').replace('**','').replace('^','')
            if unit in ('kgm-2s-1','mms-1','mm/s'):factor=86400
            elif unit in ('mm/day','mm/d','mmd-1','mmday-1','kgm-2d-1','kgm-2day-1','kgm-2','mm'):factor=1
            else:raise ValueError(f'{file.name}: unsupported units {v.units}')
            for j,m in enumerate(members):
                if m not in data:continue
                if assigned and assigned!=m and ens is None:raise ValueError(f'{file.name}: member folder and file metadata disagree')
                if ens is not None:index[ens]=j
                vals=np.ma.asarray(v[tuple(index)],dtype=float).filled(np.nan).reshape(-1)*factor
                if len(vals)!=len(times):raise ValueError('Unexpected point series shape')
                for t,val in zip(times,vals):
                    if not np.isfinite(val) or val<0:raise ValueError(f'{file.name}: missing or negative {field} at {date360(t)}')
                    if t in data[m]:raise ValueError(f'Overlapping {field} records for {m} {date360(t)}')
                    data[m][int(t)]=float(val)
                sources.append(dict(file=file.name,field=field,member=m,**cell))
    return data,sources


def load_climate(cfg,base,key,first,last):
    import re
    e,n=map(float,cfg['site_bng_m'])
    if not (math.isfinite(e) and math.isfinite(n)):raise ValueError('Invalid BNG coordinates')
    wanted=list(PPE) if cfg['members']=='all' else [member(m) for m in cfg['members']]
    if not wanted or len(set(wanted))!=len(wanted) or set(wanted)-set(PPE):raise ValueError('Select unique UKCP18 PPE member IDs or all.')
    rain,pet=({m:{} for m in wanted} for _ in range(2));sources=[]
    if cfg.get('paired_csv'):
        p=base/cfg['paired_csv']
        with p.open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f):
                m=member(r['member']);t=ordinal(r['date'])
                if m not in rain or not first<=t<=last:continue
                if abs(float(r['cell_e'])-e)>6000.1 or abs(float(r['cell_n'])-n)>6000.1:raise ValueError('CSV cell does not cover site.')
                if t in rain[m]:raise ValueError('Duplicate member/date in CSV')
                rain[m][t],pet[m][t]=float(r['rain_mm']),float(r['pet_mm'])
        sources=[{'paired_csv':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]
    else:
        def files(folder):
            return [(p,p.parent.name if re.fullmatch(r'\d{2}',p.parent.name) else None) for p in sorted((base/folder).rglob('*.nc'))]
        rf=files(cfg['rain_folder']) if cfg.get('rain_folder') else [(p,None) for p in fetch_rain(base/'climate_cache',e,n,first//360,last//360,key)]
        pf=files(cfg['pet_folder'])
        rain,a=nc_series(rf,'rain',e,n,wanted,first,last)
        pet,b=nc_series(pf,'pet',e,n,wanted,first,last)
        for m in wanted:
            cells={(s['E'],s['N']) for s in a+b if s['member']==m}
            if len(cells)>1:raise ValueError(f'Rain/PET grid centres differ for member {m}: {cells}')
        sources=a+b
    for m in wanted:
        for t in range(first,last+1):
            if not all(math.isfinite(v.get(t,math.nan)) and v[t]>=0 for v in (rain[m],pet[m])):
                raise ValueError(f'Missing or invalid climate for member {m} on {date360(t)}')
    return rain,pet,sources
