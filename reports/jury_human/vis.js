(full) => { const out=[]; const W=innerWidth,H=innerHeight;
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while(n=walker.nextNode()){ const t=n.textContent.trim(); if(!t) continue; const el=n.parentElement; if(!el) continue;
  const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
  if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0||cs.opacity==='0') continue;
  const inView = r.bottom>0 && r.top<H && r.right>0 && r.left<W;
  if(full || inView) out.push((inView?'':'[off] ')+Math.round(r.left)+','+Math.round(r.top)+' '+t.slice(0,300)); }
 return out.join('\n'); }
