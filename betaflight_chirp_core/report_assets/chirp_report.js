// Mountable Chirp report renderer. SINGLE SOURCE shared by the self-contained
// HTML report (inlined by report.py, classic <script>) and any web front
// (imported for side-effect, then window.mountChirpReport()).
// Defined as a global-exposing IIFE so it loads in BOTH a classic script and an
// ES module bundle. Entry: mountChirpReport(host, R, opts) — `R` is the assembled
// report dict (passes + _glossary + _strings); opts.fileName labels the banner.
// Returns unmount() which detaches listeners and clears host.
(function (global) {
function mountChirpReport(host, R, opts) {
opts = opts || {};
const FILE = opts.fileName || R.file_name || 'report';
host.classList.add('chirp-report');
host.innerHTML =
    '<button class="cr-langbtn langbtn"></button>'
  + '<div class="cr-hdr banner"></div>'
  + '<div class="cr-root"></div>'
  + '<div class="cr-ptip ptip"></div>'
  + '<div class="cr-htip"></div>';
const langbtnEl = host.querySelector('.cr-langbtn');
const hdrEl = host.querySelector('.cr-hdr');
const ptipEl = host.querySelector('.cr-ptip');
const htipEl = host.querySelector('.cr-htip');
const GL = R._glossary || {};
const STR = R._strings || {};
const PASSES = R.passes || [];
const PRIMARY = R.primary_index || 0;
const PRI = PASSES[PRIMARY] || {};
const CFG = PRI.config || {};
const GATE = (R.gate != null ? R.gate : 0.8);
const RESID_OK = (R.residual_ok_db != null ? R.residual_ok_db : 6);
const PAL = ['#7686a0','#9ad','#80cbc4','#ba9cff','#f48fb1','#aed581','#ffb74d','#4fc3f7'];
let W = 880; const Hh = 150, PAD = 46;   // W is recomputed responsively at each render()
let LANG = R.lang || 'fr';
const onErr = e => {
  const d=document.createElement('pre'); d.style.color='#ff8a80';
  d.textContent=(T('render_err'))+e.message+(e.lineno?(' ('+e.lineno+')'):'');
  host.appendChild(d);
};
window.addEventListener('error', onErr);
function T(k) { const s=STR[LANG]||STR.fr||{}; return (k in s)? s[k] : k; }
function tip(k,label) { const g=GL[k]||{}; const t=(g[LANG]||g.fr||'').replace(/"/g,'&quot;');
  return '<span class="term" data-tip="'+t+'">'+(label||k)+'</span>'; }
function loc(o) { return o ? (o[LANG]||o.fr||o.en||'') : ''; }
function passLabel(p) { return T('pass_word')+' '+p.n+' — '+p.ts+(p.file?(' ('+p.file+')'):''); }
function cfgFields(cfg) {
  if (!cfg) return [];
  const o=[];
  for (const ax of ['roll','pitch','yaw']) { const p=(cfg.pids||{})[ax]; if (p) o.push([ax+' P/I/D', p.join('/')]); }
  if (cfg.d_max) o.push(['D_max', cfg.d_max.join('/')]);
  const lpf=(key,lbl)=>{ const d=cfg[key]||{}; const v=(d.dyn||d.static); if(v!=null){ const vs=Array.isArray(v)?v.join('–'):v; o.push([lbl,(vs+' Hz '+(d.type||'')).trim()]); } };
  lpf('gyro_lpf1','gyro LPF1'); lpf('gyro_lpf2','gyro LPF2'); lpf('dterm_lpf1','D-term LPF1'); lpf('dterm_lpf2','D-term LPF2');
  const dn=cfg.dyn_notch||{}; if(dn.count!=null) o.push(['dyn_notch','×'+dn.count+' Q'+dn.q+' ['+dn.min+'–'+dn.max+' Hz]']);
  if(cfg.rpm_harmonics!=null) o.push(['RPM filter','×'+cfg.rpm_harmonics]);
  return o;
}
function el(tag,cls,html) { const e=document.createElement(tag); if(cls)e.className=cls; if(html!=null)e.innerHTML=html; return e; }
function mkCanvas(parent,h) { const c=document.createElement('canvas'); c.width=W; c.height=h; parent.appendChild(c); return c; }
function lerp(v,a,b,A,B) { return A + (v-a)*(B-A)/((b-a)||1); }
function logx(f,fmin,fmax) { return lerp(Math.log10(f), Math.log10(fmin), Math.log10(fmax), PAD, W-12); }
function drawAxes(ctx,h,fmin,fmax,ymin,ymax,ylabel) {
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif'; ctx.lineWidth=1;
  for (let k=0;k<=4;k++) { const yv=ymin+(ymax-ymin)*k/4, y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke();
    ctx.fillText(yv.toFixed(ymax-ymin>=10?0:1), 4, y+3); }
  for (let d=Math.floor(Math.log10(fmin)); d<=Math.ceil(Math.log10(fmax)); d++) for (const m of [1,2,5]) {
    const f=m*Math.pow(10,d); if (f<fmin||f>fmax) continue; const x=logx(f,fmin,fmax);
    ctx.strokeStyle='#20242e'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
    ctx.fillStyle='#8893a5'; ctx.fillText(f>=1000?(f/1000)+'k':f, x-6, h-8); }
  ctx.fillStyle='#9ecbff'; ctx.fillText(ylabel, PAD, 7);
}
function drawAxesLin(ctx,h,xmax,ymin,ymax,ylabel,ystep,xminor) {
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif'; ctx.lineWidth=1;
  if (ystep) { for (let yv=ymin; yv<=ymax+1e-9; yv+=ystep) { const y=lerp(yv,ymin,ymax,h-22,8);  // fixed 0.25 grid so 1.0 is always a line
      ctx.strokeStyle='#2a2f3a'; ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke();
      ctx.fillStyle='#8893a5'; ctx.fillText(yv.toFixed(2), 4, y+3); } }
  else for (let k=0;k<=4;k++) { const yv=ymin+(ymax-ymin)*k/4, y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.fillText(yv.toFixed(2), 4, y+3); }
  // faint minor x gridlines (e.g. every 10 ms) so the rise/settle timing can be gauged by eye
  if (xminor) for (let xv=xminor; xv<xmax; xv+=xminor) { const x=lerp(xv,0,xmax,PAD,W-12);
    ctx.strokeStyle='#23272f'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke(); }
  for (let k=0;k<=5;k++) { const xv=xmax*k/5, x=lerp(xv,0,xmax,PAD,W-12);
    ctx.strokeStyle='#3a4150'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
    ctx.fillStyle='#8893a5'; ctx.fillText(xv.toFixed(0)+(k===5?' ms':''), x-6, h-8); }
  ctx.fillStyle='#9ecbff'; ctx.fillText(ylabel, PAD, 7);
}
function plotLine(ctx,h,F,Y,coh,fmin,fmax,ymin,ymax,color,opts) {
  opts=opts||{}; const lw=opts.lw||1.8;
  for (let i=1;i<F.length;i++) {
    const trusted = coh[i]>=GATE && coh[i-1]>=GATE;
    ctx.globalAlpha = opts.dim ? 0.5 : 1;
    ctx.strokeStyle = trusted ? color : (opts.dim?'rgba(120,130,150,0.15)':'rgba(120,130,150,0.35)');
    ctx.lineWidth = trusted ? lw : 1;
    ctx.beginPath();
    ctx.moveTo(logx(F[i-1],fmin,fmax), lerp(Y[i-1],ymin,ymax,h-22,8));
    ctx.lineTo(logx(F[i],fmin,fmax),   lerp(Y[i],ymin,ymax,h-22,8));
    ctx.stroke();
  }
  ctx.globalAlpha=1;
}
function plotLin(ctx,h,X,Y,xmax,ymin,ymax,color,opts) {
  opts=opts||{}; ctx.globalAlpha=opts.dim?0.5:1; ctx.strokeStyle=color; ctx.lineWidth=opts.lw||1.8;
  ctx.beginPath();
  for (let i=0;i<X.length;i++) { const px=lerp(X[i],0,xmax,PAD,W-12), py=lerp(Y[i],ymin,ymax,h-22,8);
    i?ctx.lineTo(px,py):ctx.moveTo(px,py); }
  ctx.stroke(); ctx.globalAlpha=1;
}
// Inter-sweep variability band: shaded min/max envelope (lo..hi) on a log-frequency x-axis.
function plotBand(ctx,h,F,lo,hi,fmin,fmax,ymin,ymax,color) {
  ctx.beginPath();
  for (let i=0;i<F.length;i++) { const x=logx(F[i],fmin,fmax),y=lerp(hi[i],ymin,ymax,h-22,8); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }
  for (let i=F.length-1;i>=0;i--) { const x=logx(F[i],fmin,fmax),y=lerp(lo[i],ymin,ymax,h-22,8); ctx.lineTo(x,y); }
  ctx.closePath(); ctx.fillStyle=color; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1;
}
// Same, on the linear time x-axis of the step response.
function plotBandLin(ctx,h,X,lo,hi,xmax,ymin,ymax,color) {
  ctx.beginPath();
  for (let i=0;i<X.length;i++) { const x=lerp(X[i],0,xmax,PAD,W-12),y=lerp(hi[i],ymin,ymax,h-22,8); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }
  for (let i=X.length-1;i>=0;i--) { const x=lerp(X[i],0,xmax,PAD,W-12),y=lerp(lo[i],ymin,ymax,h-22,8); ctx.lineTo(x,y); }
  ctx.closePath(); ctx.fillStyle=color; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1;
}
// Zoomed inset (incrustation) in the lower-right of the step canvas: the first transient — x from 0 to
// when the curve comes back to 1 (~20-25 ms), y windowed around 1 (≈0.75–1.25, widened to the data) so
// the overshoot/return shape is legible without cramming the whole settle into the main plot.
function stepInset(ctx,h,sser,d,pcol) {
  const recross=(t,y)=>{ let pk=0; for(let i=1;i<y.length;i++) if(y[i]>y[pk]) pk=i;
    if (y[pk]>1.0) { for(let i=pk;i<y.length;i++) if(y[i]<=1.0) return t[i]; }
    for(let i=0;i<y.length;i++) if(y[i]>=0.98) return t[i]; return t[t.length-1]; };
  const prim=sser.find(o=>o.primary)||sser[sser.length-1];   // window the inset on the reference pass
  let xz=recross(prim.p.step.t_ms,prim.p.step.y)*1.3||25;
  // y-window around 1: start tracking min/max only once the curve nears the target (>=0.7), so the
  // rise from 0 doesn't drag the floor down — we want the overshoot/return detail, not the whole rise.
  let lo=0.75, hi=1.25;
  sser.forEach(o=>{ const t=o.p.step.t_ms,y=o.p.step.y; let on=false;
    for(let i=0;i<t.length&&t[i]<=xz;i++){ if(y[i]>=0.7) on=true; if(on){ lo=Math.min(lo,y[i]); hi=Math.max(hi,y[i]); } } });
  if (d.step.y_hi) for(let i=0;i<d.step.t_ms.length&&d.step.t_ms[i]<=xz;i++) hi=Math.max(hi,d.step.y_hi[i]);
  lo=Math.floor(lo/0.05)*0.05; hi=Math.ceil(hi/0.05)*0.05;
  const iw=(W-PAD-12)*0.40, ih=(h-30)*0.52, x0=W-12-iw-6, y0=h-22-ih-8;
  const xp=t=>x0+(t/xz)*iw, yp=v=>y0+ih-(v-lo)/(hi-lo)*ih;
  ctx.fillStyle='rgba(13,16,22,0.92)'; ctx.strokeStyle='#3a4150'; ctx.lineWidth=1;
  ctx.fillRect(x0,y0,iw,ih); ctx.strokeRect(x0,y0,iw,ih);
  ctx.save(); ctx.beginPath(); ctx.rect(x0,y0,iw,ih); ctx.clip();
  ctx.strokeStyle='#5a6273'; ctx.setLineDash([3,2]); ctx.beginPath(); ctx.moveTo(x0,yp(1)); ctx.lineTo(x0+iw,yp(1)); ctx.stroke(); ctx.setLineDash([]);
  if (d.step.y_lo && !HIDDEN.has(PRIMARY)) { ctx.beginPath();
    for(let i=0;i<d.step.t_ms.length&&d.step.t_ms[i]<=xz;i++){ const x=xp(d.step.t_ms[i]),y=yp(d.step.y_hi[i]); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }
    for(let i=d.step.t_ms.length-1;i>=0;i--){ if(d.step.t_ms[i]>xz)continue; ctx.lineTo(xp(d.step.t_ms[i]),yp(d.step.y_lo[i])); }
    ctx.closePath(); ctx.fillStyle=pcol; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1; }
  for (const o of sser) { ctx.globalAlpha=o.primary?1:0.5; ctx.strokeStyle=PAL[o.i%PAL.length]; ctx.lineWidth=o.primary?2:1.4;
    const t=o.p.step.t_ms,y=o.p.step.y; ctx.beginPath(); let started=false;
    for(let i=0;i<t.length&&t[i]<=xz;i++){ const x=xp(t[i]),yy=yp(y[i]); started?ctx.lineTo(x,yy):ctx.moveTo(x,yy); started=true; }
    ctx.stroke(); }
  ctx.globalAlpha=1; ctx.restore();
  ctx.fillStyle='#9ecbff'; ctx.font='9px sans-serif'; ctx.fillText('zoom 0–'+xz.toFixed(0)+' ms', x0+4, y0+10);
  ctx.fillStyle='#8893a5'; ctx.fillText(hi.toFixed(2), x0+iw-26, y0+10); ctx.fillText(lo.toFixed(2), x0+iw-26, y0+ih-4);
}
// Small fixed-size canvas for the per-axis evolution sparkline grid (cadre 3).
function mkMini(parent,w,h) { const c=document.createElement('canvas'); c.width=w; c.height=h;
  c.style.margin='2px 8px 6px 0'; c.style.display='inline-block'; parent.appendChild(c); return c; }
// Hover a plotted point (stored in canvas._hpts as {x,y,t}) -> show its value in the shared #ptip.
function miniHover(canvas) {
  canvas.onmousemove=(e)=>{
    const r=canvas.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top, tip=ptipEl;
    let best=null, bd=1e9;
    for (const pt of (canvas._hpts||[])) { const dd=(pt.x-mx)*(pt.x-mx)+(pt.y-my)*(pt.y-my); if (dd<bd) { bd=dd; best=pt; } }
    if (best && bd<169) { tip.textContent=best.t; tip.style.display='block'; tip.style.left=(e.clientX+12)+'px'; tip.style.top=(e.clientY+12)+'px'; }
    else tip.style.display='none';
  };
  canvas.onmouseleave=()=>{ ptipEl.style.display='none'; };
}
// One indicator's evolution across passes: median dot + min/max whisker (when a pass has it),
// a bare dot otherwise (single-sweep pass). Null medians (e.g. no crossover) break the line.
// opts.zones = [{lo,hi,fill}] horizontal reference bands; opts.ctx_lo/ctx_hi force the y-range
// to include a context value (so a reference band stays visible even when the data is far from it).
function miniRange(pts,opts) {
  opts=opts||{}; let vals=[]; pts.forEach(p=>{ if(p.v!=null)vals.push(p.v); if(p.lo!=null)vals.push(p.lo); if(p.hi!=null)vals.push(p.hi); });
  if(opts.ctx_lo!=null)vals.push(opts.ctx_lo); if(opts.ctx_hi!=null)vals.push(opts.ctx_hi);
  if(!vals.length) return null;
  let ymin=Math.min(...vals), ymax=Math.max(...vals);
  if(ymax-ymin<1e-6) { ymax+=1; ymin-=1; }
  const pad=(ymax-ymin)*0.14; return [ymin-pad, ymax+pad];
}
function miniSeries(ctx,pts,xpos,ypos,color,dash) {
  ctx.setLineDash(dash||[]); ctx.strokeStyle=color; ctx.globalAlpha=0.5; ctx.lineWidth=1;
  ctx.beginPath(); let started=false;
  pts.forEach((p,i)=>{ if(p.v==null){started=false;return;} const x=xpos(i),y=ypos(p.v); started?ctx.lineTo(x,y):ctx.moveTo(x,y); started=true; });
  ctx.stroke(); ctx.globalAlpha=1; ctx.setLineDash([]);
  pts.forEach((p,i)=>{ const x=xpos(i);
    if(p.lo!=null&&p.hi!=null&&p.hi-p.lo>1e-9) { const y0=ypos(p.lo),y1=ypos(p.hi);
      ctx.strokeStyle=color; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(x,y0); ctx.lineTo(x,y1);
      ctx.moveTo(x-3,y0); ctx.lineTo(x+3,y0); ctx.moveTo(x-3,y1); ctx.lineTo(x+3,y1); ctx.stroke(); }
    if(p.v!=null) { ctx.fillStyle=color; ctx.beginPath(); ctx.arc(x,ypos(p.v),2.6,0,7); ctx.fill(); } });
}
function drawMini(canvas,title,pts,color,opts) {
  opts=opts||{};
  const ctx=canvas.getContext('2d'), cw=canvas.width, ch=canvas.height;
  const L=34, Rr=10, Tt=18, Bb=16, unit=opts.unit||'';
  ctx.clearRect(0,0,cw,ch); ctx.font='10px sans-serif';
  ctx.fillStyle=color; ctx.fillText(title,4,12);   // title in the indicator colour (shared identity)
  const rg=miniRange(pts,opts);
  if(!rg) { ctx.fillStyle='#5a6273'; ctx.fillText('—',L,ch/2); return; }
  const [ymin,ymax]=rg, n=pts.length;
  const xpos=i=> n>1 ? L+(cw-L-Rr)*i/(n-1) : (L+cw-Rr)/2;
  const ypos=v=> (ch-Bb)-(v-ymin)/(ymax-ymin)*(ch-Bb-Tt);
  // reference zones (e.g. Ms healthy band) behind everything, clipped to the visible range
  for (const z of (opts.zones||[])) { const y1=ypos(Math.min(z.hi,ymax)), y0=ypos(Math.max(z.lo,ymin));
    if(y0>y1){ ctx.fillStyle=z.fill; ctx.fillRect(L,y1,cw-Rr-L,y0-y1); } }
  ctx.strokeStyle='#2a2f3a'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(L,Tt); ctx.lineTo(L,ch-Bb); ctx.lineTo(cw-Rr,ch-Bb); ctx.stroke();
  ctx.fillStyle='#8893a5'; const dec=(ymax-ymin>=10)?0:1;
  ctx.fillText(ymax.toFixed(dec)+unit,2,Tt+7); ctx.fillText(ymin.toFixed(dec)+unit,2,ch-Bb+2);  // unit recalled on the ordinate
  miniSeries(ctx,pts,xpos,ypos,color,opts.dash);
  ctx.fillStyle='#8893a5'; pts.forEach((p,i)=>ctx.fillText(p.n, xpos(i)-3, ch-4));
  canvas._hpts=pts.map((p,i)=> p.v!=null ? {x:xpos(i), y:ypos(p.v), t:p.v.toFixed(dec)+unit} : null).filter(Boolean);
  miniHover(canvas);
}
// Two indicators sharing one tile (independent left/right y-axes): A = left, solid; B = right, dashed.
// Title = the two labels in their own colour, each UNDERLINED with its line style (solid A / dashed B),
// so no "(plein)/(tireté)" words are needed. uA/uB are the units recalled on each ordinate.
function drawMini2(canvas,lA,lB,ptsA,ptsB,colA,colB,uA,uB) {
  uA=uA||''; uB=uB||'';
  const ctx=canvas.getContext('2d'), cw=canvas.width, ch=canvas.height;
  const L=24, Rr=24, Tt=18, Bb=16;
  ctx.clearRect(0,0,cw,ch); ctx.font='10px sans-serif';
  // label A (solid underline) · label B (dashed underline)
  ctx.fillStyle=colA; ctx.fillText(lA,4,11); const wA=ctx.measureText(lA).width;
  ctx.strokeStyle=colA; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(4,14); ctx.lineTo(4+wA,14); ctx.stroke();
  ctx.fillStyle='#8893a5'; ctx.fillText(' · ',4+wA,11); const wS=ctx.measureText(' · ').width, xB=4+wA+wS;
  ctx.fillStyle=colB; ctx.fillText(lB,xB,11); const wB=ctx.measureText(lB).width;
  ctx.strokeStyle=colB; ctx.setLineDash([3,2]); ctx.beginPath(); ctx.moveTo(xB,14); ctx.lineTo(xB+wB,14); ctx.stroke(); ctx.setLineDash([]);
  const ra=miniRange(ptsA), rb=miniRange(ptsB);
  if(!ra && !rb) { ctx.fillStyle='#5a6273'; ctx.fillText('—',L,ch/2); return; }
  const n=ptsA.length;
  const xpos=i=> n>1 ? L+(cw-L-Rr)*i/(n-1) : (L+cw-Rr)/2;
  ctx.strokeStyle='#2a2f3a'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(L,Tt); ctx.lineTo(L,ch-Bb); ctx.lineTo(cw-Rr,ch-Bb); ctx.stroke();
  const hp=[];
  if(ra) { const [aMin,aMax]=ra, yA=v=>(ch-Bb)-(v-aMin)/(aMax-aMin)*(ch-Bb-Tt);
    ctx.fillStyle='#8893a5'; ctx.fillText(aMax.toFixed(0)+uA,0,Tt+7); ctx.fillText(aMin.toFixed(0)+uA,0,ch-Bb+2);
    miniSeries(ctx,ptsA,xpos,yA,colA);
    ptsA.forEach((p,i)=>{ if(p.v!=null) hp.push({x:xpos(i), y:yA(p.v), t:p.v.toFixed(0)+uA}); }); }
  if(rb) { const [bMin,bMax]=rb, yB=v=>(ch-Bb)-(v-bMin)/(bMax-bMin)*(ch-Bb-Tt);
    ctx.fillStyle='#8893a5'; ctx.fillText(bMax.toFixed(0)+uB,cw-Rr+2,Tt+7); ctx.fillText(bMin.toFixed(0)+uB,cw-Rr+2,ch-Bb+2);
    miniSeries(ctx,ptsB,xpos,yB,colB,[3,2]);
    ptsB.forEach((p,i)=>{ if(p.v!=null) hp.push({x:xpos(i), y:yB(p.v), t:p.v.toFixed(0)+uB}); }); }
  ctx.fillStyle='#8893a5'; ptsA.forEach((p,i)=>ctx.fillText(p.n, xpos(i)-3, ch-4));
  canvas._hpts=hp; miniHover(canvas);
}
function hline(ctx,h,val,ymin,ymax,color,label) {
  const y=lerp(val,ymin,ymax,h-22,8); ctx.strokeStyle=color; ctx.setLineDash([4,3]);
  ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle=color; ctx.fillText(label, W-70, y-3);
}
function vline(ctx,h,f,fmin,fmax,color,label) {
  if (!f || f<fmin || f>fmax) return;
  const x=logx(f,fmin,fmax); ctx.strokeStyle=color; ctx.lineWidth=1; ctx.setLineDash([2,3]);
  ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke(); ctx.setLineDash([]);
  if (label) { ctx.fillStyle=color; ctx.fillText(label, x+2, 16); }
}
function vband(ctx,h,f0,f1,fmin,fmax,color) {
  if (!f0||!f1) return; const a=logx(Math.max(f0,fmin),fmin,fmax), b=logx(Math.min(f1,fmax),fmin,fmax);
  if (b<=a) return; ctx.fillStyle=color; ctx.fillRect(a,8,b-a,h-30);
}
function filterOverlay(ctx,h,fmin,fmax,fms) {
  if (CFG.dyn_notch) vband(ctx,h,CFG.dyn_notch.min,CFG.dyn_notch.max,fmin,fmax,'rgba(255,212,121,0.07)');
  if (CFG.gyro_lpf1 && CFG.gyro_lpf1.dyn) { vline(ctx,h,CFG.gyro_lpf1.dyn[0],fmin,fmax,'#5a9bd4','gyroLPF'); vline(ctx,h,CFG.gyro_lpf1.dyn[1],fmin,fmax,'#5a9bd4',''); }
  if (CFG.dterm_lpf1 && CFG.dterm_lpf1.dyn) { vline(ctx,h,CFG.dterm_lpf1.dyn[0],fmin,fmax,'#d48fd4','dtermLPF'); vline(ctx,h,CFG.dterm_lpf1.dyn[1],fmin,fmax,'#d48fd4',''); }
  vline(ctx,h,fms,fmin,fmax,'#ffab40','f(Ms)');
}
// Frequency where coherence drops below the gate for good (the trusted-band edge): scan for the
// first point past which it stays under GATE for a small window, so a single dip doesn't trip it.
function trustEdge(F,coh) {
  if (!F || !F.length) return null;
  const n=F.length, win=Math.max(3,Math.floor(n*0.04));
  for (let i=0;i<n-win;i++) { let below=true;
    for (let j=i;j<i+win;j++) if (coh[j]>=GATE) { below=false; break; }
    if (below) return F[i]; }
  return F[n-1];
}
// Shade the un-trusted (coherence < gate) region and mark the edge — echoed on coh, gain & phase so
// the eye sees the flat gain sits inside the trusted band.
function coherZone(ctx,h,ftrust,fmin,fmax,label) {
  if (ftrust && ftrust<fmax) vband(ctx,h,ftrust,fmax,fmin,fmax,'rgba(126,138,160,0.11)');
  vline(ctx,h,ftrust,fmin,fmax,'#8a93a5',label||'');
}
const root=host.querySelector('.cr-root');
const single = R.total_passes<=1;
const HIDDEN = new Set();   // pass indices whose overlay curves are hidden (pill toggles, global)

// --- Shared visual identity: one colour + pictogram per INDICATOR and per CONFIG item, reused
// everywhere they are named (tune score, evolution tiles, config tooltip, comparison table) so the
// eye links them at a glance. Filter colours match the Bode overlay (gyro/dterm/notch). ---
const IND={
  overshoot:{c:'#ff7a6b',p:'▲'}, rise:{c:'#ffc14d',p:'↑'}, settle:{c:'#59c2b0',p:'↓'},
  margin:{c:'#6fd36f',p:'∠'}, ms:{c:'#b58cff',p:'◎'}, noise:{c:'#4fa3e0',p:'≈'}
};
function citem(lbl) {
  if (/P\/I\/D/.test(lbl)) return {c:'#9ecbff',p:'⚙'};
  if (/D_max/.test(lbl))    return {c:'#ffab40',p:'▲'};
  if (/gyro/i.test(lbl))    return {c:'#5a9bd4',p:'∿'};
  if (/D-term/i.test(lbl))  return {c:'#d48fd4',p:'∿'};
  if (/notch/i.test(lbl))   return {c:'#ffd479',p:'▽'};
  if (/RPM/i.test(lbl))     return {c:'#aed581',p:'⟳'};
  return {c:'#9ad',p:'·'};
}
// A pass's config as coloured+pictogram HTML, for the rich hover tooltip on any pass label. Each
// field shows from→to (underlined) vs the previous pass, so a glance reveals exactly what moved.
function cfgHTML(p) {
  const fields=cfgFields(p.config||{});
  let s='<b style="color:#cfe3ff">'+(LANG==='fr'?'Passe ':'Pass ')+p.n+'</b>'
    +(p.file?' <span style="color:#8893a5">'+p.file+'</span>':'');
  if (!fields.length) return s+'<div style="color:#8893a5">'+(LANG==='fr'?'(config non lue dans ce log)':'(no config parsed)')+'</div>';
  const idx=PASSES.indexOf(p);
  const prev=(idx>0)?Object.fromEntries(cfgFields(PASSES[idx-1].config||{})):null;
  if (prev) s+=' <span style="color:#8893a5">— Δ '+(LANG==='fr'?'vs passe ':'vs pass ')+PASSES[idx-1].n+'</span>';
  s+='<div style="margin-top:4px">';
  for (const [lbl,val] of fields) {
    const ci=citem(lbl), changed=prev && prev[lbl]!=null && prev[lbl]!==val;
    const shown=changed ? ('<u style="color:#ffd479">'+prev[lbl]+' → '+val+'</u>') : val;
    s+='<div style="color:'+ci.c+'">'+ci.p+' '+lbl+' : <span style="color:#e6eaf2">'+shown+'</span></div>';
  }
  return s+'</div>';
}

// Teaching example for the throttle×freq map tooltip: a synthetic BAD map drawn with the SAME colour
// formula as the real one — a rising motor harmonic (freq grows with throttle), its 2nd harmonic, and a
// FIXED-frequency frame resonance, over a slightly raised floor; annotations baked in. Memoised data-URI.
let _mockuri=null;
function mockMapURI() {
  if (_mockuri) return _mockuri;
  const NT=9, NF=64, W0=300, H0=130, cv=document.createElement('canvas'); cv.width=W0; cv.height=H0;
  const ctx=cv.getContext('2d'), ff=i=>20+480*i/(NF-1), g=(x,c,w)=>Math.exp(-0.5*((x-c)/w)**2);
  const M=[]; let lo=1e9, hi=-1e9;
  for (let r=0;r<NT;r++) { const t=0.15+0.85*r/(NT-1), row=[];
    for (let i=0;i<NF;i++) { const fr=ff(i);
      let v=-33 + 2*Math.sin(i*1.7+r);              // raised, mildly noisy floor
      v+=34*g(fr,110+320*t,17) + 18*g(fr,220+640*t,16) + 30*g(fr,230,11);
      row.push(v); lo=Math.min(lo,v); hi=Math.max(hi,v); }
    M.push(row); }
  const Lx=26, cw=(W0-Lx)/NF, chh=(H0-24)/NT;
  for (let r=0;r<NT;r++) for (let i=0;i<NF;i++) { const tn=(M[r][i]-lo)/((hi-lo)||1);
    ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(120*Math.max(0,1-Math.abs(tn-0.5)*2))+','+Math.round(255*(1-tn))+')';
    ctx.fillRect(Lx+i*cw, 6+(NT-1-r)*chh, cw+1, chh+1); }
  ctx.fillStyle='#c9d2e0'; ctx.font='8px sans-serif'; ctx.fillText('throttle ↑',1,12); ctx.fillText('freq →',W0-32,H0-2);
  ctx.fillStyle='#fff'; ctx.font='bold 9px sans-serif';
  ctx.fillText('↗ moteur', Lx+NF*cw*0.60, 20); ctx.fillText('│ résonance', Lx+2, H0-14);
  _mockuri=cv.toDataURL('image/png'); return _mockuri;
}
function mapTipHTML() {
  return '<b style="color:#cfe3ff">'+T('mapex_h')+'</b>'
    +'<img src="'+mockMapURI()+'" style="display:block;margin:6px 0;border-radius:4px;width:300px">'
    +'<div style="color:#c2cad6;max-width:300px;white-space:normal">'+T('mapex_cap')+'</div>';
}

// Per-pass show/hide pills, repeated top-right of every axis block. They drive the global HIDDEN
// set, so toggling a pass here hides its overlaid curves across the whole report.
function passPills() {
  if (single) return null;
  const wrap=el('div','passpills');
  PASSES.forEach((p,i)=>{
    const off=HIDDEN.has(i), col=PAL[i%PAL.length];
    const b=document.createElement('button');
    b.className='pillbtn passtip'+(off?' off':''); b.textContent='P'+p.n;
    b.dataset.pass=i;
    b.style.borderColor=col; b.style.color=off?'#6b7689':col;
    b.onclick=()=>{ off?HIDDEN.delete(i):HIDDEN.add(i); render(); };
    wrap.appendChild(b);
  });
  return wrap;
}

function render() {
  root.innerHTML='';
  W = Math.max(320, Math.min(1760, (host.clientWidth || window.innerWidth) - 24));   // responsive: fill the host
  hdrEl.innerHTML =
      '<div class="banner-main"><span class="banner-icon">∿</span>'
    + '<div><div class="banner-title">'+T('title')+'</div>'
    + '<div class="banner-sub">'+T('subtitle')+'</div></div></div>'
    + '<div class="banner-tags"><span class="chip">Chirp</span><span class="chip">Analysis</span>'
    + '<span class="chip">Betaflight</span><span class="chip">Tuning</span></div>'
    + '<div class="banner-file">— '+FILE+'</div>';
  langbtnEl.textContent = T('lang_btn');

  // ---- Guide ----
  {
    const g=el('div','axis guide'); let s='<h2><span class=sicon>🧭</span>'+T('guide_h')+'</h2>';
    s+='<div class=pipe>'+T('pipe').split(' | ').map((x,i)=>'<b><span class=nidx>'+String(i+1).padStart(2,'0')+'</span>'+x+'</b>').join('<span class=arr>▸</span>')+'</div>';
    s+='<p>'+T('guide_order')
        .replace('{filt}',tip('filtering',T('w_filt')))
        .replace('{pid}',tip('pid',T('w_pid')))
        .replace('{phase}',tip('phase',T('w_phase')))
        .replace('{pm}',tip('phase_margin',T('w_pm')))
        .replace('{res}',tip('resonance',T('w_res')))+'</p>';
    s+='<p class=meta>'+T('guide_vsag')+'</p>';
    g.innerHTML=s; root.appendChild(g);
  }

  // ---- TUNE score (composite 0-100 + delta vs previous pass: better/worse after a config change) ----
  if (PRI.tune_score && PRI.tune_score.overall!=null) {
    const ts=PRI.tune_score;
    const box=el('div','axis score'); let s='<h2><span class=sicon>🎯</span>'+T('score_h')+'</h2>';
    let dtxt='';
    const prev=(PRIMARY>0 && PASSES[PRIMARY-1] && PASSES[PRIMARY-1].tune_score) ? PASSES[PRIMARY-1].tune_score : null;
    if (prev && prev.overall!=null) {
      const dv=Math.round((ts.overall-prev.overall)*10)/10;
      const col=dv>0?'#7ddf7d':(dv<0?'#ff8a80':'#8893a5'), ar=dv>0?'▲':(dv<0?'▼':'=');
      dtxt='<span class=scoredelta style="color:'+col+'">'+ar+' '+(dv>0?'+':'')+dv+' '+T('score_vs')+'</span>';
    }
    s+='<div class=scoreband><span class=scorebig>'+ts.overall.toFixed(0)+'<span class=scoremax>/100</span></span>'
     + '<span class=scoregrade>'+ts.grade+'</span>'+dtxt+'</div>';
    // Per-axis detail as a table: one column per indicator. Labels (header) carry the indicator's
    // colour + pictogram (same identity as the evolution tiles); values stay white, in their own cells.
    const SUBL={overshoot:'overshoot', rise:T('sc_rise'), margin:T('sc_margin'), ms:'Ms', noise:T('sc_noise')};
    const subKeys=Object.keys(SUBL).filter(k=>Object.keys(ts.axes).some(ax=>ts.axes[ax].subs[k]!=null));
    let head='<tr><th></th><th style="color:#9ecbff">'+T('score_h')+'</th>';
    for (const k of subKeys) head+='<th style="color:'+IND[k].c+'">'+IND[k].p+' '+SUBL[k]+'</th>';
    head+='</tr>';
    let rows='';
    for (const ax of Object.keys(ts.axes)) {
      const a=ts.axes[ax];
      rows+='<tr><td><b>'+ax+'</b></td><td><b style="color:#9ecbff">'+a.score.toFixed(0)+'</b></td>';
      for (const k of subKeys) rows+='<td>'+(a.subs[k]!=null?a.subs[k]:'—')+'</td>';
      rows+='</tr>';
    }
    s+='<table class=scoretab>'+head+rows+'</table>';
    // every pass's overall score (small), with a star on the best — the comparative view at a glance
    const scored=PASSES.map((p,i)=>({n:p.n, i:i, v:(p.tune_score&&p.tune_score.overall)})).filter(o=>o.v!=null);
    if (scored.length>1) {
      const best=Math.max(...scored.map(o=>o.v));
      const line=scored.map(o=>'<span class="passtip" data-pass="'+o.i+'" style="color:'+PAL[o.i%PAL.length]+'">P'+o.n+' ('+o.v.toFixed(0)+')</span>'
        +(o.v===best?'<span style="color:#ffd479"> ★</span>':'')).join('  ·  ');
      s+='<div class="meta scoreall">'+T('score_all')+' '+line+'</div>';
    }
    s+='<p class=meta>'+T('score_cap')+'</p>';
    box.innerHTML=s; root.appendChild(box);
  }

  // ---- Per-axis indicator evolution (right after the score: it shows how each sub-metric moved
  // pass to pass, backing up the single number above) ----
  {
    // One colour AND one pattern (solid) for every axis — the axes are told apart by their labelled
    // row, not by style. A second pattern (dashed) is used only inside the dual tile to separate its
    // two curves. Hover a point to read its value.
    const sm=(d,k)=>(d.step&&d.step.metrics)?d.step.metrics[k]:null;
    // Ms healthy/danger reference bands (cf. glossary): 1.3–2 = sain, >2 = nerveux/peu robuste.
    const MSZONES=[{lo:1.3,hi:2.0,fill:'rgba(120,200,120,0.14)'},{lo:2.0,hi:9,fill:'rgba(255,120,120,0.12)'}];
    // each tile carries the shared INDICATOR identity (colour+picto from IND), so a column links to
    // the same-coloured sub-score in the tune note above. Axes (rows) are told apart by their label.
    const INDIC=[
      {k:'s', key:'overshoot', t:'overshoot', u:'%', g:d=>sm(d,'overshoot_pct'), r:d=>d.overshoot_range},
      {k:'s', key:'rise', t:(LANG==='fr'?'montée':'rise'), u:'ms', g:d=>sm(d,'rise_ms'), r:d=>d.rise_range},
      {k:'s', key:'settle', t:(LANG==='fr'?'établiss.':'settle'), u:'ms', g:d=>sm(d,'settle_ms'), r:d=>d.settle_range},
      {k:'d', uA:'°', uB:'Hz', gA:d=>d.pm_guaranteed_deg, rA:d=>d.pm_guaranteed_range, gB:d=>d.f_ms_hz, rB:d=>d.f_ms_range},
      {k:'s', key:'ms', t:'Ms', u:'', g:d=>d.ms, r:d=>d.ms_range, opts:{ctx_lo:1.0, ctx_hi:2.1, zones:MSZONES}},
    ];
    const axesSet=[]; PASSES.forEach(p=>Object.keys(p.axes||{}).forEach(a=>{ if(!axesSet.includes(a)) axesSet.push(a); }));
    const ord=['roll','pitch','yaw']; axesSet.sort((a,b)=>ord.indexOf(a)-ord.indexOf(b));
    if (axesSet.length) {
      const box=el('div','axis'); root.appendChild(box);
      box.appendChild(el('h2',null,'<span class=sicon>📈</span>'+T('evo_h')));
      box.appendChild(el('div','meta',T('evo_cap')));
      const mw=Math.max(170, Math.min(260, Math.floor((W-30)/3)-10)), mh=128;
      const ptsFor=(axis,g,r)=>PASSES.map(p=>{ const d=(p.axes||{})[axis]; const v=d?g(d):null; const rg=d?r(d):null;
        return {n:p.n, v:(v==null?null:v), lo:rg?rg[0]:null, hi:rg?rg[1]:null}; });
      for (const axis of axesSet) {
        box.appendChild(el('div','passleg','<b style="border-bottom:2.5px solid #6b7689;padding-bottom:2px">'+axis.toUpperCase()+'</b>'));
        const grid=el('div'); grid.style.lineHeight='0'; box.appendChild(grid);
        for (const ind of INDIC) {
          if (ind.k==='d') {   // dual tile: margin (green, solid) + f(Ms) (purple, dashed) — indicator colours
            const A=ptsFor(axis,ind.gA,ind.rA), B=ptsFor(axis,ind.gB,ind.rB);
            if (A.some(p=>p.v!=null)||B.some(p=>p.v!=null)) drawMini2(mkMini(grid,mw,mh), (LANG==='fr'?'marge':'margin'), 'f(Ms)', A, B, IND.margin.c, IND.ms.c, ind.uA, ind.uB);
          } else {
            const col=IND[ind.key].c, pts=ptsFor(axis,ind.g,ind.r);
            const o2=Object.assign({}, ind.opts||{}, {unit:ind.u||''});
            if (pts.some(p=>p.v!=null)) drawMini(mkMini(grid,mw,mh), IND[ind.key].p+' '+ind.t, pts, col, o2);
          }
        }
      }
    }
  }

  // (the former "current settings" cadre is dropped — the config is now in the pass-label tooltips
  //  and the settings-comparison table below.)

  // ---- Settings comparison (sits where the overview used to: the config diff across passes,
  //      changed cells highlighted; the per-metric evolution is already shown in the tiles above) ----
  if (!single) {
    const ref=PASSES.map(p=>cfgFields(p.config||{})).filter(a=>a.length).slice(-1)[0]||[];
    if (ref.length) {
      const box=el('div','axis step cmp'); root.appendChild(box);
      box.appendChild(el('h2',null,'<span class=sicon>🔀</span>'+T('cmp_h')));
      let changedAny=false, t='<table class=cmp><tr><th></th>';
      PASSES.forEach((p,i)=>{ t+='<th><span class=swatch style="background:'+PAL[i%PAL.length]+'"></span><span class="passtip" data-pass="'+i+'">'+T('pass_word')+' '+p.n+'</span></th>'; });
      t+='</tr>';
      for (const [lbl] of ref) {
        const ci=citem(lbl);
        t+='<tr><td class=lbl><span style="color:'+ci.c+'">'+ci.p+'</span> '+lbl+'</td>'; let prev=null;
        PASSES.forEach(p=>{ const m=Object.fromEntries(cfgFields(p.config||{})); const v=(lbl in m)?m[lbl]:'—';
          const chg=(prev!==null && v!==prev); if(chg)changedAny=true;
          t+='<td'+(chg?' class=chg':'')+'>'+v+'</td>'; prev=v; });
        t+='</tr>';
      }
      t+='</table>';
      if (!changedAny) t+='<p class=meta>'+T('cmp_none')+'</p>';
      box.appendChild(el('div',null,t));
    }
  }

  // ---- Sanity check: chirp sweep spectrogram (first — confirms the measurement actually swept the
  //      whole band before any tuning read; its own cadre, ahead of Filtering) ----
  {
    const sg=PRI.spectrogram;
    if (sg && sg.levels_db && sg.levels_db.length) {
      const box=el('div','axis'); root.appendChild(box);
      box.appendChild(el('h2',null,'<span class=sicon>🔍</span>'+tip('spectrogram',T('sanity_h'))+' <span class=meta>('+sg.axis+' gyro)</span>'));
      const rows=sg.levels_db.length, cols=sg.levels_db[0].length;
      const cw=W-PAD-12, Hs=Math.max(220,rows*1.6), cellW=cw/cols, cellH=(Hs-30)/rows;
      const ctx=mkCanvas(box,Hs).getContext('2d'); ctx.clearRect(0,0,W,Hs);
      const lo=-28, hi=0;   // fixed window for contrast: cells within 28 dB of each column's max
      for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {
        const v=sg.levels_db[r][c]; const tn=Math.max(0,Math.min(1,(v-lo)/((hi-lo)||1)));
        ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(150*Math.max(0,1-Math.abs(tn-0.55)*2))+','+Math.round(255*(1-tn))+')';
        ctx.fillRect(PAD+c*cellW, 8+(rows-1-r)*cellH, cellW+1, cellH+1);
      }
      ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif';
      // log frequency axis: decade ticks (1/2/5) placed by log position
      const fmn=sg.freqs[0], fmx=sg.freqs[sg.freqs.length-1];
      const lyy=fv=>8+(1-(Math.log10(fv)-Math.log10(fmn))/(Math.log10(fmx)-Math.log10(fmn)))*(Hs-30);
      for (let d=Math.floor(Math.log10(fmn)); d<=Math.ceil(Math.log10(fmx)); d++) for (const mm of [1,2,5]) {
        const fv=mm*Math.pow(10,d); if (fv<fmn||fv>fmx) continue;
        ctx.fillText(fv>=1000?(fv/1000)+'k':fv, 4, lyy(fv)+3); }
      const tmaxS=sg.t_s[sg.t_s.length-1]-sg.t_s[0];
      for (let k=0;k<=5;k++) { const x=PAD+k/5*cw; ctx.fillText((tmaxS*k/5).toFixed(1)+(k===5?' s':''), x-6, Hs-6); }
      ctx.fillStyle='#9ecbff'; ctx.fillText('freq (Hz) ↑   temps →', PAD, Hs-18);
      let scap=T('spectro_cap').replace('{sg}',tip('spectrogram','spectrogramme')).replace('{ax}',sg.axis);
      if (sg.n_sweeps) scap+=' '+(LANG==='fr'
        ? 'Médiane de '+sg.n_sweeps+' sweeps (alignés sur le temps relatif) — la crête est plus nette, le bruit inter-sweeps moyenné.'
        : 'Median of '+sg.n_sweeps+' sweeps (aligned on relative time) — sharper ridge, inter-sweep noise averaged out.');
      box.appendChild(el('div','legend',scap));
    }
  }

  // ---- Step 1: Filtering ----
  {
    const box=el('div','axis step'); root.appendChild(box);
    box.appendChild(el('h2',null,'<span class=sicon>🧹</span>'+tip('filtering',T('step1_h'))));
    const tm=PRI.throttle_map;
    if (tm && tm.freqs && tm.freqs.length) {
      box.appendChild(el('h3',null,tip('throttle_map',T('tmap_h'))+' ('+tm.axis+' gyro · '+(tm.source||'?')+')'
        +' <span class="maptip" title="">?</span>'));
      const rows=tm.levels_db.length, cols=tm.freqs.length;
      // Robust colour scale: anchor to the 10th–98th percentiles, not the absolute min/max. With raw
      // min/max a single quiet cell drags the floor down and the whole map saturates red even when the
      // noise is fairly uniform — a contrast artefact, not "noisy everywhere". Percentiles fix that:
      // a calm map reads blue/green, only genuine hot-spots (top ~2%) go red.
      const flat=tm.levels_db.flat().filter(v=>v!==null).sort((a,b)=>a-b);
      const lo=flat[Math.floor(flat.length*0.10)], hi=flat[Math.floor(flat.length*0.98)];
      const cw=W-PAD-12, chh=22, H2=rows*chh+30;
      const ctx=mkCanvas(box,H2).getContext('2d'); ctx.clearRect(0,0,W,H2);
      for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {
        const v=tm.levels_db[r][c]; if (v===null) continue; const tn=Math.max(0,Math.min(1,(v-lo)/((hi-lo)||1)));
        ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(120*Math.max(0,1-Math.abs(tn-0.5)*2))+','+Math.round(255*(1-tn))+')';
        ctx.fillRect(PAD+c*cw/cols, 8+(rows-1-r)*chh, cw/cols+1, chh);
      }
      ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif';
      for (let r=0;r<rows;r++) ctx.fillText(tm.throttle_bins[r], 4, 8+(rows-1-r)*chh+14);
      const fmin=tm.freqs[0], fmax=tm.freqs[cols-1];
      for (let d=Math.floor(Math.log10(fmin));d<=Math.ceil(Math.log10(fmax));d++) for (const m of [1,2,5]) {
        const f=m*Math.pow(10,d); if (f<fmin||f>fmax) continue;
        const x=PAD+(Math.log10(f)-Math.log10(fmin))/(Math.log10(fmax)-Math.log10(fmin))*cw;
        ctx.fillText(f>=1000?(f/1000)+'k':f, x-6, H2-6); }
      ctx.fillStyle='#9ecbff'; ctx.fillText('throttle ↑   freq (Hz) →', PAD, H2-18);
      const tmx=f=>PAD+(Math.log10(f)-Math.log10(fmin))/(Math.log10(fmax)-Math.log10(fmin))*cw;
      const tvl=(f,col,lab)=>{ if(!f||f<fmin||f>fmax)return; const x=tmx(f);
        ctx.strokeStyle=col; ctx.setLineDash([3,3]); ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,H2-26); ctx.stroke(); ctx.setLineDash([]);
        if(lab){ctx.fillStyle=col; ctx.fillText(lab,x+2,18);} };
      if (CFG.dyn_notch) { tvl(CFG.dyn_notch.min,'#ffd479','dyn_notch'); tvl(CFG.dyn_notch.max,'#ffd479',''); }
      for (const su of (PRI.filter_suggestions||[])) tvl(su.freq_hz,'#ff8a80','rés');
      box.appendChild(el('div','howto','<span class=meta>'+T('tmap_lo')+'</span><span class=scalebar></span><span class=meta>'+T('tmap_hi')+'</span>'));
      box.appendChild(el('div','howto',T('tmap_howto')));
    } else {
      box.appendChild(el('p','meta',tip('throttle_map',T('tmap_h'))+' — '+T('tmap_none')));
    }

    // noise spectrum (raw vs filtered PSD, dB) — drives the filtering decision
    const ns=PRI.noise_spectrum;
    if (ns && ns.freqs && ns.freqs.length) {
      box.appendChild(el('h3',null,tip('noise_psd',T('noise_h'))+' ('+ns.axis+' gyro)'));
      const F=ns.freqs, fmin=Math.max(30,F[0]), fmax=F[F.length-1];
      // floor-relative axis: 0 = noise floor. Scale to the noise region (95th pct) so a stray
      // low-freq motion bump doesn't squash the plot.
      const sorted=ns.raw_db.slice().sort((a,b)=>a-b); const hiR=sorted[Math.floor(sorted.length*0.97)];
      let lo=Math.max(-25,Math.min(-6,...ns.filt_db)), hi=Math.max(12,Math.ceil(hiR/5)*5+3);
      const H3=180, nc=mkCanvas(box,H3).getContext('2d');
      drawAxes(nc,H3,fmin,fmax,lo,hi,'dB/plancher');
      if (CFG.dyn_notch) vband(nc,H3,CFG.dyn_notch.min,CFG.dyn_notch.max,fmin,fmax,'rgba(255,212,121,0.07)');
      nc.font='10px sans-serif';
      // motor-harmonic bands (from eRPM): where motor noise lives -> a peak in a band is motor noise
      const mh=ns.motor;
      if (mh && mh.bands) for (const b of mh.bands) {
        vband(nc,H3,b.lo,b.hi,fmin,fmax,'rgba(255,138,80,0.12)');
        if (b.hi>fmin && b.lo<fmax) { nc.fillStyle='#ff9a6a'; nc.fillText(b.n+'×', logx(Math.max(b.lo,fmin),fmin,fmax)+1, H3-24); }
      }
      // vertical lines = each filter's cut-off frequency (the LPF starts attenuating above it)
      const vcut=(fc,col,lab,yl)=>{ if(!fc||fc<fmin||fc>fmax)return; const x=logx(fc,fmin,fmax);
        nc.strokeStyle=col; nc.lineWidth=1; nc.setLineDash([3,3]); nc.beginPath(); nc.moveTo(x,8); nc.lineTo(x,H3-22); nc.stroke(); nc.setLineDash([]);
        if(lab){ nc.fillStyle=col; nc.fillText(lab,Math.min(x+2,W-44),yl||16); } };
      if (CFG.gyro_lpf1 && CFG.gyro_lpf1.dyn) { vcut(CFG.gyro_lpf1.dyn[0],'#5a9bd4'); vcut(CFG.gyro_lpf1.dyn[1],'#5a9bd4','gLPF1',16); }
      if (CFG.gyro_lpf2) vcut(CFG.gyro_lpf2.static,'#79c0ff','gLPF2',16);
      if (CFG.dterm_lpf1 && CFG.dterm_lpf1.dyn) vcut(CFG.dterm_lpf1.dyn[1],'#d48fd4','dLPF1',28);
      if (CFG.dterm_lpf2) vcut(CFG.dterm_lpf2.static,'#d48fd4','dLPF2',28);
      hline(nc,H3,0,lo,hi,'#7e8aa0','plancher');                              // 0 dB = noise floor
      hline(nc,H3,RESID_OK,lo,hi,'#ff8a80','+'+RESID_OK+' dB');  // indicative residual-resonance guide
      const ones=F.map(_=>1);
      if (ns.has_unfilt) plotLine(nc,H3,F,ns.filt_db,ones,fmin,fmax,lo,hi,'#80cbc4',{lw:1.6});
      plotLine(nc,H3,F,ns.raw_db,ones,fmin,fmax,lo,hi,'#4fc3f7',{lw:1.8});
      nc.font='10px sans-serif'; let _lab=0;
      for (const pk of (ns.peaks||[])) { if (pk.freq_hz<fmin||pk.freq_hz>fmax) continue;
        const x=logx(pk.freq_hz,fmin,fmax), y=lerp(pk.above_floor_db,lo,hi,H3-22,8);
        nc.fillStyle='#ffd479'; nc.beginPath(); nc.arc(x,y,2.6,0,7); nc.fill();
        if (pk.above_floor_db >= RESID_OK) { const dy=(_lab++ %2)?12:-3;  // stagger to avoid overlap
          nc.fillText(pk.freq_hz.toFixed(0)+'Hz +'+pk.above_floor_db.toFixed(0)+'dB', x+4, y+dy); } }
      box.appendChild(el('div','legend',
        (ns.has_unfilt?('<span style="color:#4fc3f7">— '+T('leg_raw')+'</span><span style="color:#80cbc4">— '+T('leg_filt')+'</span>'):'<span style="color:#4fc3f7">— gyro</span>')+
        '<span style="color:#7e8aa0">-- '+T('leg_floor')+'</span>'+
        '<span style="color:#ff8a80">-- '+T('leg_resid')+'</span>'+
        '<span style="color:#5a9bd4">| '+tip('gyro_lpf','coupures gyro LPF')+'</span>'+
        '<span style="color:#d48fd4">| '+tip('dterm_lpf','coupures D-term LPF')+'</span>'+
        '<span style="color:#ffd479">▮ '+tip('dyn_notch','dyn_notch')+'</span>'+
        (ns.motor?'<span style="color:#ff9a6a">▮ '+tip('motor_harmonics',T('leg_motor'))+'</span>':'')));
      box.appendChild(el('div','legend',(ns.has_unfilt?T('noise_cap'):T('noise_cap_nounfilt')).replace('{psd}',tip('noise_psd','PSD'))));
    }

    const fsug=PRI.filter_suggestions||[], nsug=PRI.noise_suggestions||[];
    let s='<details class="coll"><summary class="collh">'+tip('resonance',T('filt_h'))+'</summary><ul class="sugg filt">';
    for (const x of fsug) s+='<li>'+loc(x)+'</li>';
    for (const x of nsug) s+='<li>'+loc(x)+'</li>';
    if (!fsug.length && !nsug.length) s+='<li>—</li>';
    s+='</ul></details>'; box.appendChild(el('div',null,s));
  }

  // ---- PID per axis (Bode + step response, all passes overlaid). No standalone section header:
  //      each axis block ("PID Roll/Pitch/Yaw") is its own cadre. ----
  for (const axis of Object.keys(PRI.axes||{})) {
    const d=PRI.axes[axis]; if(!d||!d.freq) continue;
    const box=el('div','axis'); root.appendChild(box);
    const m=d.phase_margin_deg, fco=d.crossover_hz, mu=d.phase_margin_unc_deg;
    const ms=d.ms, fms=d.f_ms_hz, pmg=d.pm_guaranteed_deg;
    let mtxt;
    if (ms!=null) {
      // Robust scalars only: Ms, f(Ms) and the guaranteed margin. The 0 dB crossover
      // ("bandwidth") and the measured margin are dropped here — on very damped axes the
      // crossover detection breaks down and reports nonsense (e.g. 2 Hz / 165°). The Bode
      // plots below still carry the full picture.
      mtxt = tip('sensitivity','Ms')+' '+ms.toFixed(2)+' @ '+(fms?fms.toFixed(0):'?')+' Hz'
           + ' · '+tip('phase_margin',T('pm_gtd'))+' ≥'+pmg.toFixed(0)+'°';
    } else {
      mtxt = m==null ? T('no_xover') : (tip('phase_margin',T('margin'))+' '+m.toFixed(0)+'°'+(mu?(' ±'+mu.toFixed(0)+'°'):'')+' @ '+(fco?fco.toFixed(0):'?')+' Hz');
    }
    box.appendChild(el('h2',null,'<span class=sicon>🎛️</span>PID '+axis.charAt(0).toUpperCase()+axis.slice(1)+' <span class=meta>['+d.band_hz[0]+'–'+d.band_hz[1]+' Hz] — '+mtxt+'</span>'));
    if (!single) box.appendChild(el('div','meta',T('overlay')+' <i>('+T('overlay_hint')+')</i>'));
    const pills=passPills(); if (pills) box.appendChild(pills);
    const fmin=d.band_hz[0]||1, fmax=d.band_hz[1]||500;
    const ser=PASSES.map((p,i)=>({p:p.axes&&p.axes[axis], i:i, primary:i===PRIMARY})).filter(o=>o.p&&o.p.freq&&!HIDDEN.has(o.i));
    const PCOL=PAL[PRIMARY%PAL.length];   // primary pass colour, used for its inter-sweep band

    const wrap=v=>((v%360)+360)%360-360;
    // the trusted-band edge (coherence < gate), read on the primary pass and echoed on every plot
    const ftrust = trustEdge(d.freq, d.coherence);
    const trustLbl = (LANG==='fr'?'zone non fiable':'untrusted zone');

    // 1) Coherence first — it defines where the rest can be trusted; the 0.8 gate edge carries down.
    // The reliability note sits next to the title; the grey zone is labelled in-plot.
    box.appendChild(el('h3',null,tip('coherence',LANG==='fr'?'Cohérence':'Coherence')
      +' <span class="meta" style="text-transform:none;letter-spacing:0;font-weight:400">— '
      +T('coh_cap').replace('{gate}',GATE.toFixed(1))+'</span>'));
    let ch=mkCanvas(box,Hh-30).getContext('2d');
    drawAxes(ch,Hh-30,fmin,fmax,0,1,'coh');
    coherZone(ch,Hh-30,ftrust,fmin,fmax,trustLbl);
    hline(ch,Hh-30,GATE,0,1,'#7e8aa0',GATE.toFixed(1));
    if (d.coherence_band && !HIDDEN.has(PRIMARY)) plotBand(ch,Hh-30,d.freq,d.coherence_band[0],d.coherence_band[1],fmin,fmax,0,1,PCOL);
    for (const o of ser) plotLine(ch,Hh-30,o.p.freq,o.p.coherence,o.p.coherence.map(_=>1),fmin,fmax,0,1,PAL[o.i%PAL.length],{dim:!o.primary, lw:o.primary?2:1.3});

    // 2) Gain — filter-overlay legend moved up next to the title (the grey untrusted zone is still
    //    echoed from coherence on the plot, but no longer needs its own legend entry).
    const bodeLeg='<span style="text-transform:none;letter-spacing:0;font-weight:400;font-size:11px;margin-left:12px">'
      +'<span style="color:#5a9bd4;margin-right:12px">│ '+tip('gyro_lpf',T('leg_gyro'))+'</span>'
      +'<span style="color:#d48fd4;margin-right:12px">│ '+tip('dterm_lpf',T('leg_dterm'))+'</span>'
      +'<span style="color:#ffd479;margin-right:12px">▮ '+tip('dyn_notch',T('leg_notch'))+'</span>'
      +'<span style="color:#ffab40">│ '+tip('sensitivity',T('leg_fms'))+'</span></span>';
    box.appendChild(el('h3',null,tip('gain',T('bode_h'))+bodeLeg));
    let gAll=[]; ser.forEach(o=>gAll=gAll.concat(o.p.gain_db));
    if (d.gain_band) gAll=gAll.concat(d.gain_band[0],d.gain_band[1]);
    let gmin=Math.min(-12,...gAll), gmax=Math.max(12,...gAll);
    let g=mkCanvas(box,Hh).getContext('2d');
    drawAxes(g,Hh,fmin,fmax,gmin,gmax,'gain dB');
    coherZone(g,Hh,ftrust,fmin,fmax,'');
    filterOverlay(g,Hh,fmin,fmax,fms);
    hline(g,Hh,0,gmin,gmax,'#5a6273','0 dB');
    if (d.gain_band && !HIDDEN.has(PRIMARY)) plotBand(g,Hh,d.freq,d.gain_band[0],d.gain_band[1],fmin,fmax,gmin,gmax,PCOL);
    for (const o of ser) plotLine(g,Hh,o.p.freq,o.p.gain_db,o.p.coherence,fmin,fmax,gmin,gmax,PAL[o.i%PAL.length],{dim:!o.primary, lw:o.primary?2.2:1.5});

    // 3) Phase — same trusted-zone overlay.
    box.appendChild(el('h3',null,tip('phase',LANG==='fr'?'Phase':'Phase')));
    let p=mkCanvas(box,Hh).getContext('2d');
    drawAxes(p,Hh,fmin,fmax,-360,0,'phase °');
    coherZone(p,Hh,ftrust,fmin,fmax,'');
    hline(p,Hh,-180,-360,0,'#ff8a80','-180°');
    if (d.phase_band && !HIDDEN.has(PRIMARY)) plotBand(p,Hh,d.freq,d.phase_band[0].map(wrap),d.phase_band[1].map(wrap),fmin,fmax,-360,0,PCOL);
    for (const o of ser) plotLine(p,Hh,o.p.freq,o.p.phase_deg.map(wrap),o.p.coherence,fmin,fmax,-360,0,PAL[o.i%PAL.length],{dim:!o.primary, lw:o.primary?2.2:1.5});
    vline(p,Hh,fms,fmin,fmax,'#ffab40','f(Ms)');

    // step response (time domain)
    const sser=ser.filter(o=>o.p.step && o.p.step.t_ms && o.p.step.t_ms.length);
    if (sser.length) {
      box.appendChild(el('h3',null,tip('step_response',T('step_h'))));
      // Full window on the main plot; y normalised to 0.25 steps so 1.0 is always a gridline.
      let xmax=0, ymax=1.0; sser.forEach(o=>{ xmax=Math.max(xmax,o.p.step.t_ms[o.p.step.t_ms.length-1]); ymax=Math.max(ymax,...o.p.step.y); });
      if (d.step.y_hi) ymax=Math.max(ymax,...d.step.y_hi);
      ymax=Math.ceil(ymax/0.25)*0.25;
      let st=mkCanvas(box,Hh).getContext('2d');
      drawAxesLin(st,Hh,xmax,0,ymax,'step',0.25,10);   // minor gridlines every 10 ms
      hline(st,Hh,1,0,ymax,'#5a6273','1.0');
      // rise time is measured 10% → 90% of the final value; show those two thresholds (labels left,
      // away from the lower-right inset) so the "rise X ms" number is self-explanatory.
      st.font='10px sans-serif';
      [[0.1,'10%'],[0.9,'90%']].forEach(([v,lb])=>{ const y=lerp(v,0,ymax,Hh-22,8);
        st.strokeStyle='#3f4856'; st.setLineDash([2,3]); st.beginPath(); st.moveTo(PAD,y); st.lineTo(W-12,y); st.stroke(); st.setLineDash([]);
        st.fillStyle='#6b7689'; st.fillText(lb, PAD+3, y-2); });
      if (d.step.y_lo && !HIDDEN.has(PRIMARY)) plotBandLin(st,Hh,d.step.t_ms,d.step.y_lo,d.step.y_hi,xmax,0,ymax,PCOL);
      for (const o of sser) plotLin(st,Hh,o.p.step.t_ms,o.p.step.y,xmax,0,ymax,PAL[o.i%PAL.length],{dim:!o.primary, lw:o.primary?2.2:1.5});
      stepInset(st,Hh,sser,d,PCOL);   // zoomed incrustation on the rise/overshoot (lower-right)
      const mt=d.step&&d.step.metrics;
      if (mt) box.appendChild(el('div','legend',T('metrics').replace('{ov}',mt.overshoot_pct).replace('{rise}',mt.rise_ms==null?'–':mt.rise_ms).replace('{settle}',mt.settle_ms==null?'–':mt.settle_ms)));
    }
    // inter-sweep repeatability: median values are shown above; here is the measured min/max spread
    if (d.n_sweeps) {
      const rg=a=>a&&a[0]!=null?('['+a[0]+'–'+a[1]+']'):'–';
      const fr='Répétabilité sur '+d.n_sweeps+' sweeps (bande ombrée = étendue min/max inter-sweeps) — overshoot '+rg(d.overshoot_range)+' %, montée '+rg(d.rise_range)+' ms, Ms '+rg(d.ms_range)+', marge '+rg(d.phase_margin_range)+'°.';
      const en='Repeatability over '+d.n_sweeps+' sweeps (shaded band = inter-sweep min/max range) — overshoot '+rg(d.overshoot_range)+' %, rise '+rg(d.rise_range)+' ms, Ms '+rg(d.ms_range)+', margin '+rg(d.phase_margin_range)+'°.';
      box.appendChild(el('div','legend',LANG==='fr'?fr:en));
    }

    // (per-axis textual diagnosis intentionally omitted here — redundant with the evolution tiles
    // at the top; the observations remain in the text/JSON output for the LLM.)
  }

  // ---- Glossary ----
  {
    const order=['chirp','gain','phase','sensitivity','phase_margin','crossover','coherence','resonance',
      'noise_psd','motor_harmonics','filtering','gyro_lpf','dterm_lpf','dyn_notch','rpm_filter','dmax','pid','throttle_map','spectrogram','step_response','propwash'];
    const box=el('div','axis'); root.appendChild(box);
    let s='<details class="coll"><summary class="collh2"><span class=sicon>📖</span>'+T('glossary_h')+'</summary><dl class=glos>';
    // entries sorted alphanumerically by their displayed term name (in the active language)
    const entries=[];
    for (const k of order) { const g=GL[k]; if (g && (g[LANG]||g.fr)) {
      const txt=(g[LANG]||g.fr); entries.push({head:txt.split(/ : | — |: /)[0], txt:txt}); } }
    entries.sort((a,b)=>a.head.localeCompare(b.head, LANG, {numeric:true, sensitivity:'base'}));
    for (const e of entries) s+='<dt>'+e.head+'</dt><dd>'+e.txt+'</dd>';
    s+='</dl></details>'; box.innerHTML=s;
  }
}
langbtnEl.onclick=()=>{ LANG = (LANG==='fr'?'en':'fr'); render(); };
let _rt; const onResize=()=>{ clearTimeout(_rt); _rt=setTimeout(render, 150); };
window.addEventListener('resize', onResize);
// Cursor-positioned HTML tooltip: pass config on a pass label (.passtip[data-pass]),
// or the good/bad throttle-map teaching example on the '?' badge (.maptip).
const onMove = e=>{
  const ht=htipEl;
  const pe=e.target.closest && e.target.closest('.passtip[data-pass]');
  const me=e.target.closest && e.target.closest('.maptip');
  if (pe) ht.innerHTML=cfgHTML(PASSES[+pe.dataset.pass]);
  else if (me) ht.innerHTML=mapTipHTML();
  else { ht.style.display='none'; return; }
  ht.style.display='block';
  ht.style.left=Math.min(e.clientX+14, window.innerWidth-ht.offsetWidth-12)+'px';
  ht.style.top=Math.min(e.clientY+14, window.innerHeight-ht.offsetHeight-12)+'px';
};
host.addEventListener('mousemove', onMove);
render();
return function unmount(){
  window.removeEventListener('resize', onResize);
  window.removeEventListener('error', onErr);
  host.removeEventListener('mousemove', onMove);
  host.innerHTML='';
};
}
global.mountChirpReport = mountChirpReport;
})(typeof window !== 'undefined' ? window : globalThis);
