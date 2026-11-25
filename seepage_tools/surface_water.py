"""Apply the daily soil-water balance and compute pumping for an XSLOPE section."""
from pathlib import Path
import csv
import json
import math
import numpy as np
from climate_data import date360


def positive(value,label,zero=False):
    x=float(value)
    if not math.isfinite(x) or (x<0 if zero else x<=0):raise ValueError('Invalid '+label)
    return x


def daily_balance(rain,pet,pars):
    capacity=positive(pars['capacity_mm'],'soil capacity')
    cap=positive(pars['infiltration_limit_mm_day'],'infiltration limit',True)
    cover=positive(pars['et_factor'],'ET factor',True)
    fraction=positive(pars['initial_fraction'],'initial soil fraction',True)
    if fraction>1:raise ValueError('initial_fraction must be from 0 to 1')
    store=capacity*fraction
    rows=[]
    for p,pe in zip(rain,pet,strict=True):
        if min(p,pe)<0 or not all(map(math.isfinite,(p,pe))):raise ValueError('Invalid rainfall or PET')
        before=store
        infiltration=min(p,cap)
        aet=min(store+infiltration,pe*cover)
        available=store+infiltration-aet
        drainage=max(available-capacity,0.)
        store=available-drainage
        runoff=p-infiltration
        residual=p-aet-runoff-drainage-(store-before)
        if abs(residual)>1e-8:raise ArithmeticError('Daily surface balance failed')
        rows.append((p,pe,before,aet,runoff,drainage,store,residual))
    return rows


def ground_weights(nodes,edges,ranges,blocked):
    """Integrate shape functions over horizontal projection of exterior upper edges."""
    nodes=np.asarray(nodes); weights=np.zeros(len(nodes))
    exterior=[(a,b,m) for a,b,m,count in edges if count==1 and abs(nodes[a,0]-nodes[b,0])>1e-9]
    for a,b,m in exterior:
        xa,ya=nodes[a];xb,yb=nodes[b];x=(xa+xb)/2;y=(ya+yb)/2
        if any(min(nodes[u,0],nodes[v,0])-1e-9<=x<=max(nodes[u,0],nodes[v,0])+1e-9 and
               nodes[u,1]+(x-nodes[u,0])*(nodes[v,1]-nodes[u,1])/(nodes[v,0]-nodes[u,0])>y+1e-7
               for u,v,_ in exterior):continue
        ids=[a,b] if m<0 else [a,b,m]
        if set(ids)&blocked:continue
        for left,right in ranges:
            lo=max(min(xa,xb),left);hi=min(max(xa,xb),right)
            if hi<=lo:continue
            # Gauss integration of linear or quadratic basis over the clipped edge.
            for xi in [-1/math.sqrt(3),1/math.sqrt(3)]:
                xq=(lo+hi+(hi-lo)*xi)/2
                t=(xq-xa)/(xb-xa)
                basis=[1-t,t] if m<0 else [(1-t)*(1-2*t),t*(2*t-1),4*t*(1-t)]
                weights[ids]+=(hi-lo)/2*np.array(basis)
    if weights.sum()<=0:raise ValueError('No free upper boundary edges in selected recharge range.')
    return weights


def write_csv(path,head,rows):
    with Path(path).open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(head);w.writerows(rows)


class ClimateCase:
    def __init__(self,cfg,member,start,rain,pet,out,case):
        self.cfg=cfg;self.member=member;self.start=start;self.out=out;self.case=case
        self.days=int(cfg['duration_days'])
        self.name=f"{cfg['site_name']} · {member} · {date360(start)} · {case['name']}"
        steps=range(start-int(cfg['spinup_days']),start+self.days)
        self.rain=[rain[t] for t in range(start,start+self.days)]
        self.recharge=[];audit=[]
        for z in cfg['zones']:
            rows=daily_balance([rain[t] for t in steps],[pet[t] for t in steps],z)
            self.recharge.append(np.array([r[5] for r in rows[-self.days:]])/1000)
            audit.extend((date360(t),t-start,z['name'],*row) for t,row in zip(steps,rows))
        write_csv(out/'surface_balance.csv',['climate_date','model_day','zone','rain_mm','PET_mm','initial_store_mm','actual_ET_mm','runoff_mm','drainage_mm','final_store_mm','residual_mm'],audit)

    def set_materials(self,model):
        for name,factor in self.case['K_multiplier'].items():
            matches=[m for m in model['materials'] if m['name']==name]
            if len(matches)!=1:raise ValueError('Material missing or ambiguous: '+name)
            for key in ('k1','k2'):matches[0][key]*=positive(factor,'K multiplier')

    def bind(self,sd,schedule,edges,features,transient):
        if str(sd.get('unit_system','')).lower() not in ('si','metric') or str(sd.get('time_unit','')).lower() not in ('day','days','d'):
            raise ValueError('Climate coupling requires XSLOPE length in metres and time in days.')
        if transient and not np.isclose(schedule['duration'],self.days,rtol=0,atol=1e-8):
            raise ValueError('duration_days must equal workbook transient duration.')
        self.pits=[]
        assigned=set()
        for p in self.cfg['pits']:
            labels=p['boundary_labels'];ids=set()
            for label in labels:
                match=[v for k,v in features if k==label]
                if len(match)!=1 or not match[0]:raise ValueError(f'Unknown or empty pit boundary {label}. Available: {[k for k,v in features]}')
                ids.update(match[0])
            if not ids or ids&assigned:raise ValueError('Pit selections must contain distinct, nonempty node sets.')
            assigned.update(ids);self.pits.append((p,sorted(ids)))
        # Exclude edges touching an existing condition, including pit corners.
        blocked={i for _,idx in features for i in idx}
        geom=[];intervals=[];self.weights=[]
        for i,(z,rates) in enumerate(zip(self.cfg['zones'],self.recharge,strict=True)):
            ranges=z['section_x_ranges_m']
            for a,b in ranges:
                if not all(map(math.isfinite,(a,b))) or a>=b or any(min(b,v)>max(a,u) for u,v in intervals):
                    raise ValueError('Recharge intervals must be finite, increasing and disjoint.')
                intervals.append((a,b))
            w=ground_weights(sd['nodes'],edges,ranges,blocked)
            self.weights.append(w)
            geom.extend((z['name'],j+1,*sd['nodes'][j],weight) for j,weight in enumerate(w) if weight!=0)
            features.append(('Recharge · '+z['name'],np.flatnonzero(w).tolist()))
            if transient:
                key=f'__surface_water_{i}'
                if key in schedule['series']:raise ValueError('Recharge series name conflict')
                # Two values at each interior day boundary: incoming day's value exactly
                # at the anchor, next day's value immediately after (XSLOPE step convention).
                ta=[0.];va=[rates[0]]
                for d in range(1,self.days):
                    ta.extend([d,np.nextafter(float(d),np.inf)]);va.extend([rates[d-1],rates[d]])
                ta.append(self.days);va.append(rates[-1])
                schedule['series'][key]=(np.asarray(ta),np.asarray(va))
                sd['flux_series_bindings'].append(dict(unit_flux_nodal=w,series=key,value_t0=rates[0]))
                schedule['breakpoints']=sorted(set(schedule['breakpoints'])|set(range(self.days+1)))
            else:sd['flux_nodal']+=w*rates.mean()
        write_csv(self.out/'recharge_nodes.csv',['zone','node_id','x_m','elevation_m','projected_weight_m'],geom)

    def results(self,times,dt,external,transient):
        daily=[];summary=[];plots=[]
        indices=np.flatnonzero(np.any(np.asarray(self.weights)!=0,axis=0))
        applied=np.asarray(external)[:,indices].sum(axis=1)
        expected=sum(w.sum()*r for w,r in zip(self.weights,self.recharge))
        if transient:
            measured=np.zeros(self.days)
            for end,step,q in zip(times,dt,applied,strict=True):
                d=min(self.days-1,int((end-step/2)))
                if end> d+1+1e-7 or end-step<d-1e-7:raise ValueError('Solver crossed daily forcing boundary')
                measured[d]+=step*q
            np.testing.assert_allclose(measured,expected,rtol=1e-7,atol=1e-8)
            write_csv(self.out/'recharge_audit.csv',['day_ending','expected_m3_per_m','applied_m3_per_m'],((i+1,a,b) for i,(a,b) in enumerate(zip(expected,measured))))
        else:np.testing.assert_allclose(applied[0],expected.mean(),rtol=1e-7,atol=1e-8)
        for pit,ids in self.pits:
            length=positive(pit['section_length_m'],'representative length')
            area=positive(pit['plan_area_m2'],'plan area',True)
            exposure=pit.get('exposed_area_fraction',[[0,1]])
            anchors=np.asarray(exposure,dtype=float)
            if anchors.ndim!=2 or anchors.shape[1]!=2 or not np.isfinite(anchors).all() or anchors[0,0]!=0 or np.any(np.diff(anchors[:,0])<=0) or np.any((anchors[:,1]<0)|(anchors[:,1]>1)):raise ValueError('Invalid exposed area fraction anchors')
            # Exact mean area fraction in each day under piecewise linear opening.
            fractions=[]
            for d in range(self.days):
                t=np.r_[d,anchors[(anchors[:,0]>d)&(anchors[:,0]<d+1),0],d+1]
                y=np.interp(t,anchors[:,0],anchors[:,1]);fractions.append(np.sum(np.diff(t)*(y[:-1]+y[1:])/2))
            direct=np.asarray(self.rain)*area*np.asarray(fractions)/1000
            q=np.maximum(-np.asarray(external)[:,ids],0).sum(axis=1)*length
            gw=np.zeros(self.days)
            if transient:
                for end,step,rate in zip(times,dt,q,strict=True):
                    left=end-step
                    for d in range(max(0,math.floor(left)),min(self.days,math.ceil(end))):
                        gw[d]+=rate*max(0,min(end,d+1)-max(left,d))
                np.testing.assert_allclose(gw.sum(),np.dot(q,dt),rtol=1e-9,atol=1e-8)
            else:gw[:]=q[0]
            total=gw+direct
            for d in range(self.days):daily.append((pit['name'],d+1,date360(self.start+d),gw[d],direct[d],total[d]))
            summary.append([pit['name'],float(total.max()),float(total.sum()),float(gw.sum()),float(direct.sum())])
            plots.append(dict(name=pit['name'],groundwater=gw.tolist(),rain=direct.tolist(),total=total.tolist()))
        write_csv(self.out/'pumping.csv',['pit','day_ending','climate_date','groundwater_m3','direct_rain_m3','combined_m3'],daily)
        self.summary=summary
        return dict(name=self.name,pits=plots,steady=not transient)
