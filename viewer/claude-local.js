// window.claude local: emula las capacidades db, user y downloads del runtime de artifacts
// (contrato 0.2.91) contra el servidor local. Los datos viven en <id>/_db/, el mismo layout
// que deja ArtifactData out_dir, para que la sync con claude.ai sea un diff directo.
(() => {
  const ART = document.currentScript.dataset.art;
  const API = '/_api/';
  const SEG = /^(?!\.\.?$)[A-Za-z0-9_\-.~:@+]{1,200}$/;
  const ME = {id: 'u_local_owner', name: document.currentScript.dataset.user || 'Local', email: null, isOwner: true, canEdit: true, color: '#2f5fd0',
    avatarUrl: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="16" fill="#2f5fd0"/><text x="16" y="21" font-size="14" text-anchor="middle" fill="#fff" font-family="sans-serif">W</text></svg>')};

  const err = (code, message) => Object.assign(new Error(message), {code});
  function split(path, parity) {
    const segs = String(path).split('/');
    if (segs.length > 16 || !segs.every(s => SEG.test(s))) throw new TypeError(`ruta inválida: ${path}`);
    if (segs.length % 2 !== parity) throw new TypeError(`ruta con ${segs.length} segmentos: ${path}`);
    return segs.join('/');
  }
  async function call(method, kind, path, body) {
    const r = await fetch(`${API}${kind}/${ART}?path=${encodeURIComponent(path)}`, {
      method, headers: body ? {'Content-Type': 'application/json'} : {}, body: body && JSON.stringify(body)});
    if (r.status === 400) throw err('invalid_argument', await r.text());
    if (!r.ok) throw err('unavailable', `HTTP ${r.status}`);
    return r.status === 204 ? null : r.json();
  }

  // Snapshots congelados; un documento sin cambios es el mismo objeto entre entregas.
  const cache = new Map();
  const META = Object.freeze({fromCache: false, hasPendingWrites: false});
  function snap(path, exists, data) {
    const key = JSON.stringify(data ?? null), prev = cache.get(path);
    if (prev && prev.exists === exists && prev.key === key) return prev.snap;
    const body = exists ? Object.freeze(structuredClone(data)) : undefined;
    const s = Object.freeze({id: path.split('/').pop(), exists, data: () => body, metadata: META});
    cache.set(path, {exists, key, snap: s});
    return s;
  }

  // Suscripciones: se refrescan al escribir desde esta página y, para otras pestañas,
  // cuando cambia el contador de revisión del servidor.
  const subs = new Set();
  let rev = -1;
  const refresh = () => subs.forEach(s => s.run());
  setInterval(async () => {
    if (!subs.size) return;
    try { const r = await call('GET', 'rev', ''); if (r.rev !== rev) { rev = r.rev; refresh(); } } catch {}
  }, 1200);
  function subscribe(read, next, error) {
    // Solo un error de la llamada misma es terminal; uno pasajero (servidor reiniciando) se reintenta
    // en el próximo sondeo, como hace el runtime real con `unavailable`.
    const s = {alive: true, run: () => read().then(v => s.alive && next(v), e => {
      if (!s.alive || e.code !== 'invalid_argument') return;
      s.alive = false; subs.delete(s); error ? error(e) : reportError(e);
    })};
    subs.add(s); setTimeout(s.run, 0);
    return () => { s.alive = false; subs.delete(s); };
  }
  const written = r => { setTimeout(refresh, 0); return r; };

  function docRef(path) {
    const id = path.split('/').pop();
    const get = async () => { const r = await call('GET', 'doc', path); return snap(path, r.exists, r.data); };
    return Object.freeze({
      id, path, get,
      set: data => call('PUT', 'doc', path, {data}).then(written).then(() => undefined),
      update: data => call('PATCH', 'doc', path, {data}).then(written).then(() => undefined),
      delete: () => call('DELETE', 'doc', path).then(written).then(() => undefined),
      acquire: async o => ({acquired: true, version: 1, holder: o?.holder,
        expiresAt: new Date(Date.now() + (o?.ttlMs || 30000)).toISOString()}),
      onSnapshot: (next, error) => subscribe(get, next, error),
      collection: sub => colRef(split(`${path}/${sub}`, 1)),
    });
  }

  const OPS = {
    '==': (a, b) => a === b, '!=': (a, b) => a !== b, '<': (a, b) => a < b, '<=': (a, b) => a <= b,
    '>': (a, b) => a > b, '>=': (a, b) => a >= b, 'in': (a, b) => b.includes(a), 'not-in': (a, b) => !b.includes(a),
    'array-contains': (a, b) => Array.isArray(a) && a.includes(b),
  };
  function query(path, q) {
    let last = [];
    const run = async () => {
      const rows = await call('GET', 'col', path);
      let docs = rows.map(r => snap(`${path}/${r.id}`, true, r.data));
      for (const [f, op, v] of q.where) docs = docs.filter(d => f in d.data() && OPS[op](d.data()[f], v));
      if (q.order) {
        const [f, dir] = q.order, s = dir === 'desc' ? -1 : 1;
        docs.sort((a, b) => !(f in a.data()) ? 1 : !(f in b.data()) ? -1 :
          (a.data()[f] < b.data()[f] ? -s : a.data()[f] > b.data()[f] ? s : 0));
      } else docs.sort((a, b) => (a.id < b.id ? -1 : 1));
      if (q.limit) docs = docs.slice(0, q.limit);
      // Transiciones incrementales, como Firestore: primero las bajas y después altas y movimientos sobre la
      // lista ya ajustada. Un documento que solo se corre porque salió otro no cuenta como `modified`.
      const prev = last; last = docs;
      const changes = [], view = prev.map(d => d.id), ids = new Set(docs.map(d => d.id));
      prev.forEach(d => {
        if (ids.has(d.id)) return;
        const i = view.indexOf(d.id);
        view.splice(i, 1); changes.push({type: 'removed', doc: d, oldIndex: i, newIndex: -1});
      });
      docs.forEach((d, i) => {
        const j = view.indexOf(d.id);
        if (j < 0) { view.splice(i, 0, d.id); changes.push({type: 'added', doc: d, oldIndex: -1, newIndex: i}); return; }
        const before = prev.find(p => p.id === d.id);
        if (before !== d || j !== i) {
          view.splice(j, 1); view.splice(i, 0, d.id);
          changes.push({type: 'modified', doc: d, oldIndex: j, newIndex: i});
        }
      });
      return Object.freeze({docs, size: docs.length, empty: !docs.length, docChanges: () => changes, metadata: META});
    };
    return {
      where: (f, op, v) => { if (!OPS[op]) throw err('invalid_argument', `operador ${op}`); return query(path, {...q, where: [...q.where, [f, op, v]]}); },
      orderBy: (f, dir = 'asc') => query(path, {...q, order: [f, dir]}),
      limit: n => query(path, {...q, limit: n}),
      get: () => { last = []; return run(); },
      onSnapshot: (next, error) => { const fresh = query(path, q); return subscribe(fresh._run, next, error); },
      _run: run,
    };
  }
  function colRef(path) {
    const base = query(path, {where: [], order: null, limit: 0});
    const rnd = () => crypto.randomUUID().replace(/-/g, '').slice(0, 20);
    return Object.freeze({
      ...base, path,
      doc: id => docRef(split(`${path}/${id ?? rnd()}`, 0)),
      add: async data => { const d = docRef(`${path}/${rnd()}`); await d.set(data); return d; },
    });
  }

  const db = Object.freeze({doc: p => docRef(split(p, 0)), collection: p => colRef(split(p, 1))});

  const profile = id => ({id, name: id === ME.id ? ME.name : '', avatarUrl: ME.avatarUrl, color: ME.color,
    email: null, isMe: id === ME.id, guest: false});
  const user = Object.freeze({
    isOwner: async () => true, canEdit: async () => true, can: async () => true,
    me: async () => ({...ME}), id: async () => ME.id, name: async () => ME.name,
    avatarUrl: async () => ME.avatarUrl, email: async () => null, search: async () => [],
    profiles: async ids => Object.fromEntries([].concat(ids).map(i => [i, profile(i)])),
  });

  const downloads = Object.freeze({
    save: async ({filename, data}) => {
      const blob = data instanceof Blob ? data : new Blob([data]);
      const a = Object.assign(document.createElement('a'), {href: URL.createObjectURL(blob), download: filename});
      a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    },
  });

  const caps = {db, user, downloads};
  const memo = {};
  window.claude = Object.freeze({
    local: true,
    use: name => caps[name] ? (memo[name] ??= new Promise(r => setTimeout(() => r(caps[name]), 0))) : Promise.resolve(null),
  });
})();
