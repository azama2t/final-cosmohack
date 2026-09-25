// Wind / current particles (earth.nullschool style): thin, semi-transparent trails advected through a u/v grid.
// Canvas 2D overlay above the map; one batched stroke per frame; particle count limited (≤ 1600) for ≥ 50 fps.
import { ctl } from './controller';

export interface FlowField {
  kind: 'currents' | 'wind';
  w: number;
  s: number;
  e: number;
  n: number;
  nx: number;
  ny: number;
  /** row-major, row 0 = south edge (lat = s), column 0 = west edge; NaN = no data (land) */
  u: Float32Array;
  v: Float32Array;
  maxSpeed: number;
  units: string;
  source?: string;
  time?: string;
  note?: string;
}

/** bilinear sample; null outside / on land */
export function sample(f: FlowField, lon: number, lat: number): [number, number] | null {
  if (lon < f.w || lon > f.e || lat < f.s || lat > f.n) return null;
  const fx = ((lon - f.w) / (f.e - f.w || 1)) * (f.nx - 1);
  const fy = ((lat - f.s) / (f.n - f.s || 1)) * (f.ny - 1);
  const i = Math.min(f.nx - 2, Math.max(0, Math.floor(fx)));
  const j = Math.min(f.ny - 2, Math.max(0, Math.floor(fy)));
  const tx = Math.min(1, Math.max(0, fx - i)),
    ty = Math.min(1, Math.max(0, fy - j));
  const k00 = j * f.nx + i,
    k10 = k00 + 1,
    k01 = k00 + f.nx,
    k11 = k01 + 1;
  const u00 = f.u[k00],
    u10 = f.u[k10],
    u01 = f.u[k01],
    u11 = f.u[k11];
  const v00 = f.v[k00],
    v10 = f.v[k10],
    v01 = f.v[k01],
    v11 = f.v[k11];
  if ([u00, u10, u01, u11, v00, v10, v01, v11].some((x) => !Number.isFinite(x))) {
    // nearest valid corner (coast): keeps particles moving up to the land edge
    const ks = [k00, k10, k01, k11].filter((k) => Number.isFinite(f.u[k]) && Number.isFinite(f.v[k]));
    if (!ks.length) return null;
    return [f.u[ks[0]], f.v[ks[0]]];
  }
  const u = (u00 * (1 - tx) + u10 * tx) * (1 - ty) + (u01 * (1 - tx) + u11 * tx) * ty;
  const v = (v00 * (1 - tx) + v10 * tx) * (1 - ty) + (v01 * (1 - tx) + v11 * tx) * ty;
  return [u, v];
}

export class FlowLayer {
  private cv: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private fields: FlowField[] = [];
  private px: Float32Array = new Float32Array(0);
  private py: Float32Array = new Float32Array(0);
  private age: Uint16Array = new Uint16Array(0);
  private fi: Uint8Array = new Uint8Array(0);
  private raf = 0;
  private dpr = 1;
  private moving = false;
  private last = 0;
  fps = 0;
  private frames = 0;
  private fpsT = 0;

  constructor(container: HTMLElement) {
    this.cv = document.createElement('canvas');
    this.cv.className = 'flow-canvas';
    this.cv.setAttribute('data-testid', 'flow-canvas');
    container.appendChild(this.cv);
    this.ctx = this.cv.getContext('2d')!;
    this.resize();
  }

  resize = () => {
    const map = ctl.map;
    if (!map) return;
    const c = map.getContainer();
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.cv.width = c.clientWidth * this.dpr;
    this.cv.height = c.clientHeight * this.dpr;
    this.cv.style.width = c.clientWidth + 'px';
    this.cv.style.height = c.clientHeight + 'px';
  };

  private onMoveStart = () => {
    this.moving = true;
  };
  private onMoveEnd = () => {
    this.moving = false;
  };

  setFields(fields: FlowField[]) {
    this.fields = fields.filter((f) => f && f.nx > 1 && f.ny > 1);
    const map = ctl.map;
    if (!this.fields.length || !map) return this.stop();
    const W = this.cv.width / this.dpr,
      H = this.cv.height / this.dpr;
    const n = Math.round(Math.min(1400, Math.max(400, (W * H) / 1600)));
    this.px = new Float32Array(n);
    this.py = new Float32Array(n);
    this.age = new Uint16Array(n);
    this.fi = new Uint8Array(n);
    for (let i = 0; i < n; i++) this.spawn(i, true);
    this.ctx.clearRect(0, 0, this.cv.width, this.cv.height);
    if (!this.raf) {
      map.on('movestart', this.onMoveStart);
      map.on('moveend', this.onMoveEnd);
      map.on('resize', this.resize);
      this.last = performance.now();
      this.raf = requestAnimationFrame(this.tick);
    }
  }

  private spawn(i: number, randomAge = false) {
    const k = this.fields.length > 1 ? i % this.fields.length : 0;
    const f = this.fields[k];
    this.fi[i] = k;
    // spawn inside the field ∩ current view when possible
    const map = ctl.map!;
    const b = map.getBounds();
    const w = Math.max(f.w, b.getWest()),
      e = Math.min(f.e, b.getEast()),
      s = Math.max(f.s, b.getSouth()),
      n = Math.min(f.n, b.getNorth());
    const useView = e > w && n > s;
    for (let t = 0; t < 6; t++) {
      const lon = useView ? w + Math.random() * (e - w) : f.w + Math.random() * (f.e - f.w);
      const lat = useView ? s + Math.random() * (n - s) : f.s + Math.random() * (f.n - f.s);
      if (sample(f, lon, lat)) {
        this.px[i] = lon;
        this.py[i] = lat;
        break;
      }
      this.px[i] = lon;
      this.py[i] = lat;
    }
    this.age[i] = randomAge ? Math.floor(Math.random() * 90) : 0;
  }

  private tick = (t: number) => {
    this.raf = requestAnimationFrame(this.tick);
    const map = ctl.map;
    if (!map || !this.fields.length) return;
    const dt = Math.min(0.05, (t - this.last) / 1000);
    this.last = t;
    this.frames++;
    if (t - this.fpsT > 1000) {
      this.fps = Math.round((this.frames * 1000) / (t - this.fpsT));
      this.frames = 0;
      this.fpsT = t;
    }
    const ctx = this.ctx;
    const W = this.cv.width,
      H = this.cv.height;
    // fade previous trails (stronger while the camera moves so that trails do not smear)
    ctx.globalCompositeOperation = 'destination-in';
    ctx.fillStyle = this.moving ? 'rgba(0,0,0,0.6)' : 'rgba(0,0,0,0.93)';
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over';
    // speed: max-speed particle crosses ~1/6 of the view per second; scale from the visible span
    const b = map.getBounds();
    const span = Math.max(1e-6, b.getNorth() - b.getSouth());
    const dpr = this.dpr;
    const paths: Path2D[] = this.fields.map(() => new Path2D());
    for (let i = 0; i < this.px.length; i++) {
      const f = this.fields[this.fi[i]];
      const lon = this.px[i],
        lat = this.py[i];
      const uv = sample(f, lon, lat);
      if (!uv || this.age[i] > 110) {
        this.spawn(i);
        continue;
      }
      const k = (span / 6 / Math.max(1e-6, f.maxSpeed)) * dt;
      const nlon = lon + (uv[0] * k) / Math.max(0.2, Math.cos((lat * Math.PI) / 180));
      const nlat = lat + uv[1] * k;
      const a = map.project([lon, lat]);
      const c = map.project([nlon, nlat]);
      this.px[i] = nlon;
      this.py[i] = nlat;
      this.age[i]++;
      if (a.x < -20 || a.y < -20 || a.x > W / dpr + 20 || a.y > H / dpr + 20) continue;
      const p = paths[this.fi[i]];
      p.moveTo(a.x * dpr, a.y * dpr);
      p.lineTo(c.x * dpr, c.y * dpr);
    }
    ctx.lineWidth = 1 * dpr;
    ctx.lineCap = 'round';
    this.fields.forEach((f, k) => {
      ctx.strokeStyle = f.kind === 'wind' ? 'rgba(200,204,210,0.26)' : 'rgba(110,180,225,0.55)';
      ctx.stroke(paths[k]);
    });
  };

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    const map = ctl.map;
    map?.off('movestart', this.onMoveStart);
    map?.off('moveend', this.onMoveEnd);
    map?.off('resize', this.resize);
    this.ctx.clearRect(0, 0, this.cv.width, this.cv.height);
    this.fields = [];
  }

  destroy() {
    this.stop();
    this.cv.remove();
  }
}

/**
 * Flexible parser of /api/flow answers → FlowField. Accepts {bounds:[w,s,e,n] | lon[]/lat[], nx, ny, u[], v[]}
 * (flat or 2-D arrays, rows from north or south — `origin`/`lat` order decides), plus {kind, units, source, time}.
 */
export function parseFlow(j: any, kind: 'currents' | 'wind'): FlowField | null {
  if (!j || typeof j !== 'object') return null;
  const src = j[kind] && typeof j[kind] === 'object' && (j[kind].u || j[kind].grid) ? j[kind] : j.grid ?? j;
  let u: any = src.u ?? src.U;
  let v: any = src.v ?? src.V;
  if (!u || !v) return null;
  let lons: number[] | null = Array.isArray(src.lon) ? src.lon : Array.isArray(src.lons) ? src.lons : null;
  let lats: number[] | null = Array.isArray(src.lat) ? src.lat : Array.isArray(src.lats) ? src.lats : null;
  let ny = src.ny ?? (Array.isArray(u[0]) ? u.length : lats?.length);
  let nx = src.nx ?? (Array.isArray(u[0]) ? u[0].length : lons?.length);
  const flat = (a: any): number[] => (Array.isArray(a[0]) ? (a as number[][]).flat() : (a as number[]));
  const uf = flat(u).map((x) => (x === null || x === undefined ? NaN : Number(x)));
  const vf = flat(v).map((x) => (x === null || x === undefined ? NaN : Number(x)));
  if (!nx || !ny || uf.length !== nx * ny) return null;
  let w: number, s: number, e: number, n: number;
  const bb = src.bounds ?? src.bbox ?? j.bounds ?? j.bbox;
  if (Array.isArray(bb) && bb.length === 4) [w, s, e, n] = bb;
  else if (lons && lats) {
    w = Math.min(...lons);
    e = Math.max(...lons);
    s = Math.min(...lats);
    n = Math.max(...lats);
  } else if (src.lon0 !== undefined && src.lat0 !== undefined && src.dlon && src.dlat) {
    w = src.lon0;
    s = src.lat0;
    e = w + src.dlon * (nx - 1);
    n = s + src.dlat * (ny - 1);
  } else return null;
  // row order: from north if lat array decreases, or origin says so
  const northFirst =
    (typeof src.la1 === 'number' && typeof src.la2 === 'number' && src.la1 > src.la2) ||
    (lats && lats.length > 1 && lats[0] > lats[lats.length - 1]) ||
    /north|top|upper/i.test(String(src.origin ?? src.row_order ?? ''));
  const U = new Float32Array(nx * ny),
    V = new Float32Array(nx * ny);
  for (let j2 = 0; j2 < ny; j2++) {
    const row = northFirst ? ny - 1 - j2 : j2;
    for (let i = 0; i < nx; i++) {
      U[j2 * nx + i] = uf[row * nx + i];
      V[j2 * nx + i] = vf[row * nx + i];
    }
  }
  let mx = 0;
  for (let k = 0; k < U.length; k++) if (Number.isFinite(U[k]) && Number.isFinite(V[k])) mx = Math.max(mx, Math.hypot(U[k], V[k]));
  void lons;
  void lats;
  return {
    kind,
    w,
    s,
    e,
    n,
    nx,
    ny,
    u: U,
    v: V,
    maxSpeed: mx || 1,
    units: String(src.units ?? j.units ?? (kind === 'wind' ? 'м/с' : 'м/с')),
    source: src.source ?? j.source,
    time: src.time ?? j.time,
    note: j.note ?? src.note,
  };
}
