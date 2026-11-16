// Edición de elementos en el lienzo (como en claude.ai): clic izquierdo selecciona un elemento dentro de un board,
// doble clic edita su texto, el inspector cambia su estilo y deja un comentario anclado a él. Los cambios se
// escriben en <id>/project/<board>.dc.html por /_api/board/<id> y quedan como cambio local.
//
// Cómo se ubica el elemento en el archivo: el motor de Design numera cada elemento del template de <x-dc> en
// preorden (data-dc-tpl) y lo copia a lo renderizado. Ese mismo orden es el de las etiquetas de apertura en el
// fuente, así que el elemento N del render es la etiqueta N del archivo. Se edita esa etiqueta y nada más del
// archivo. Se verifica al cargar: el fuente se parsea como lo hace el motor (template.innerHTML) y la lista de
// elementos tiene que coincidir etiqueta por etiqueta con el escaneo. Si el parser corrige algo (una tabla sin
// <tbody>, un <template> anidado, un <p> mal cerrado), la numeración se correría: ese board queda de solo lectura.
window.CanvasEdit = {
  mount({art, vp, view, comments, boardTitle, pageOf}) {
    const API = p => `/_api/board/${art}?path=${encodeURIComponent(p)}`;
    const RAW = new Set(['script', 'style', 'textarea', 'title', 'xmp', 'noscript', 'iframe', 'noembed', 'noframes', 'plaintext']);
    const STYLES = [  // [propiedad, etiqueta, tipo]
      ['color', 'Color del texto', 'color'], ['background-color', 'Fondo', 'color'],
      ['font-size', 'Tamaño de letra', 'text'], ['font-weight', 'Peso', 'weight'],
      ['padding', 'Relleno', 'text'], ['margin', 'Margen', 'text'], ['border-radius', 'Esquinas', 'text'],
    ];
    const css = document.createElement('style');
    css.textContent = `
      .ce-layer{position:absolute;inset:0;pointer-events:none;z-index:4}
      .ce-box{position:absolute;border:1.5px solid var(--acc);box-sizing:border-box}
      .ce-box.hover{border-style:dashed;opacity:.8}.ce-box.twin{border-style:dotted}
      .ce-tag{position:absolute;bottom:100%;left:-1.5px;background:var(--acc);color:var(--bar);font:600 11px/18px system-ui,sans-serif;padding:0 6px;white-space:nowrap;border-radius:3px 3px 0 0}
      .ce-panel{position:fixed;top:56px;left:8px;width:min(300px,calc(100% - 16px));max-height:calc(100% - 64px);overflow:auto;z-index:15;
        background:var(--bar);color:var(--fg);border:1px solid var(--line);border-radius:10px;box-shadow:0 6px 24px #0004;font:13px/1.4 system-ui,sans-serif}
      .ce-panel[hidden]{display:none}
      .ce-panel header{position:static;height:auto;display:flex;gap:8px;align-items:flex-start;padding:10px 12px;border-bottom:1px solid var(--line);background:none;z-index:auto}
      .ce-path{flex:1;word-break:break-word}.ce-path b{display:block;font-size:14px}.ce-path small{color:var(--mut)}
      .ce-sec{padding:10px 12px;border-bottom:1px solid var(--line);display:grid;gap:8px}
      .ce-sec h3{margin:0;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
      .ce-row{display:grid;grid-template-columns:110px 1fr;gap:8px;align-items:center}
      .ce-row div{display:flex;gap:6px}
      .ce-panel input,.ce-panel select,.ce-panel button{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 8px;min-height:28px;min-width:0}
      .ce-panel input[type=color]{padding:2px;width:36px;flex:none}.ce-panel .ce-row input[type=text]{flex:1;width:100%}
      .ce-panel button{background:transparent;cursor:pointer}.ce-panel button:disabled{opacity:.45;cursor:default}
      .ce-acts{display:flex;flex-wrap:wrap;gap:6px}
      .ce-msg{color:var(--mut);font-size:12px;min-height:16px}.ce-msg.err{color:#d0453a}
      [data-ce-editing]{outline:2px solid #2f5fd0!important;outline-offset:2px;cursor:text!important}`;
    document.head.append(css);
    const layer = Object.assign(document.createElement('div'), {className: 'ce-layer'});
    vp.append(layer);
    const panel = Object.assign(document.createElement('aside'), {className: 'ce-panel', hidden: true});
    panel.setAttribute('aria-label', 'Elemento seleccionado');
    document.body.append(panel);

    const src = {};  // path del board -> {src, sha, tags}
    const undo = [];  // [{path, src}] estado anterior de cada escritura
    let sel = null, hover = null, editing = null, msg = '', msgErr = false;

    // ---- fuente: etiquetas de apertura de <x-dc> en orden ----
    function scan(text) {
      const open = text.search(/<x-dc[\s>]/i);
      if (open < 0) return null;
      let i = text.indexOf('>', open) + 1;
      const end = text.search(/<\/x-dc\s*>/i), tags = [];
      while (i < end) {
        const lt = text.indexOf('<', i);
        if (lt < 0 || lt >= end) break;
        if (text.startsWith('<!--', lt)) { const c = text.indexOf('-->', lt + 4); i = c < 0 ? end : c + 3; continue; }
        const m = /^<([A-Za-z][\w:-]*)/.exec(text.slice(lt, lt + 80));
        if (!m) { i = lt + 1; continue; }
        let j = lt + m[0].length, q = null;  // fin de la etiqueta, respetando comillas
        for (; j < end; j++) { const ch = text[j]; if (q) { if (ch === q) q = null; } else if (ch === '"' || ch === "'") q = ch; else if (ch === '>') break; }
        const name = m[1].toLowerCase();
        tags.push({name, start: lt, tagEnd: j + 1});
        i = j + 1;
        if (RAW.has(name)) { const c = text.slice(i).search(new RegExp(`</${name}\\s*>`, 'i')); i = c < 0 ? end : i + c; }
      }
      return tags;
    }
    async function load(path, force) {
      if (src[path] && !force) return src[path];
      const r = await fetch(API(path));
      if (!r.ok) throw new Error(`no se pudo leer el board (${r.status})`);
      const d = await r.json();
      return src[path] = model(d.src, d.sha);
    }
    function model(text, sha) {
      const tags = scan(text), open = text.search(/<x-dc[\s>]/i), end = text.search(/<\/x-dc\s*>/i);
      const t = document.createElement('template');
      t.innerHTML = open < 0 ? '' : text.slice(text.indexOf('>', open) + 1, end);
      const els = [];
      (function walk(n) { if (n.nodeType === 1) els.push(n.localName.toLowerCase()); for (const c of n.childNodes) walk(c); })(t.content);
      const ok = !!tags && tags.length === els.length && tags.every((g, i) => g.name === els[i]);
      return {src: text, sha, tags, ok};
    }
    async function write(path, text, before) {
      const r = await fetch(API(path), {method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({data: {src: text, prev: src[path].sha}})});
      if (r.status === 409) { await load(path, true); throw new Error('el board cambió en disco (otra pestaña o sesión): recargué el fuente, repetí el cambio'); }
      if (!r.ok) throw new Error(`no se pudo guardar (${r.status})`);
      if (before != null) undo.push({path, src: before});
      src[path] = model(text, (await r.json()).sha);
      reloadBoard(path);
    }

    // ---- qué se puede hacer con un elemento ----
    const esc = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    function srcTag(s, idx, el) {
      const t = s?.ok && s.tags[idx];
      return t && t.name === el.localName.toLowerCase() ? t : null;
    }
    function textSpan(s, t) {  // contenido de un elemento que solo tiene texto: [inicio, fin] en el fuente
      const close = s.src.slice(t.tagEnd).search(new RegExp(`</${t.name}\\s*>`, 'i'));
      if (close < 0) return null;
      const inner = s.src.slice(t.tagEnd, t.tagEnd + close);
      return inner.includes('<') || inner.includes('{{') ? null : [t.tagEnd, t.tagEnd + close];
    }
    const STYLE_RE = /\sstyle\s*=\s*("([^"]*)"|'([^']*)')/i;
    function styleOf(tag) {
      const m = STYLE_RE.exec(tag);
      const raw = m ? (m[2] ?? m[3]).replace(/&quot;/g, '"').replace(/&amp;/g, '&') : '';
      return {m, raw, decl: decls(raw).map(d => [d.slice(0, d.indexOf(':')).trim().toLowerCase(), d.slice(d.indexOf(':') + 1).trim()])};
    }
    function decls(raw) {  // corta en «;» fuera de paréntesis y comillas: url(data:…;base64,…) queda entera
      const out = []; let cur = '', depth = 0, q = null;
      for (const ch of raw) {
        if (q) { if (ch === q) q = null; } else if (ch === '"' || ch === "'") q = ch;
        else if (ch === '(') depth++; else if (ch === ')') depth = Math.max(0, depth - 1);
        else if (ch === ';' && !depth) { out.push(cur); cur = ''; continue; }
        cur += ch;
      }
      out.push(cur);
      return out.map(d => d.trim()).filter(d => d.includes(':'));
    }
    function info(e = sel) {
      const s = src[e.path], t = srcTag(s, e.idx, e.el);
      if (!t) return {t: null, why: s && !s.ok ? 'El HTML de este board no se puede ubicar con seguridad en el archivo (el navegador lo corrige al leerlo): solo se puede comentar.' : 'Este elemento no está en el archivo tal cual (lo genera el código del board): solo se puede comentar.'};
      const tag = s.src.slice(t.start, t.tagEnd), st = styleOf(tag);
      return {t, tag, st, span: e.el.children.length ? null : textSpan(s, t), styleLocked: st.raw.includes('{{'),
        line: s.src.slice(0, t.start).split('\n').length};
    }

    // ---- edición: una escritura a la vez, cada una sobre el fuente que dejó la anterior ----
    let chain = Promise.resolve();
    const queued = fn => (chain = chain.then(fn, fn));
    const setText = (e, text) => queued(async () => {
      const s = src[e.path], {span} = info(e);
      if (!span) throw new Error('este texto no se puede editar acá');
      await write(e.path, s.src.slice(0, span[0]) + esc(text) + s.src.slice(span[1]), s.src);
    });
    const setStyle = (e, prop, value) => queued(async () => {
      if (/[;{}]/.test(value)) throw new Error('un valor por campo: sin «;» ni llaves');
      const s = src[e.path], {t, tag, st, styleLocked} = info(e);
      if (!t || styleLocked) throw new Error('el estilo de este elemento depende de datos ({{ }}): no se edita acá');
      const decl = st.decl.filter(([k]) => k !== prop);
      if (value.trim()) decl.push([prop, value.trim()]);
      const attr = decl.length ? ` style="${decl.map(([k, v]) => `${k}:${v}`).join(';').replace(/&/g, '&amp;').replace(/"/g, '&quot;')}"` : '';
      let next;
      if (st.m) next = tag.slice(0, st.m.index) + attr + tag.slice(st.m.index + st.m[0].length);
      else next = tag.replace(/\s*\/?>$/, m => attr + m);
      await write(e.path, s.src.slice(0, t.start) + next + s.src.slice(t.tagEnd), s.src);
    });
    const doUndo = () => queued(async () => {
      const u = undo.pop();
      if (!u) return;
      try { await load(u.path); await write(u.path, u.src, null); say('Deshecho.'); }
      catch (err) { undo.push(u); say(err.message, true); }
    });
    function startTextEdit(e) {
      const {span} = info(e);
      if (!span) return say('Este texto tiene elementos adentro o datos ({{ }}): seleccioná el elemento interno.', true);
      const el = e.el, before = el.textContent;
      editing = el; el.setAttribute('data-ce-editing', ''); el.contentEditable = 'plaintext-only';
      el.focus();
      const r = el.ownerDocument.createRange(); r.selectNodeContents(el);
      const ws = el.ownerDocument.defaultView.getSelection(); ws.removeAllRanges(); ws.addRange(r);
      const finish = async keep => {
        if (editing !== el) return;
        editing = null; el.removeAttribute('data-ce-editing'); el.contentEditable = 'inherit';
        el.removeEventListener('keydown', key); el.removeEventListener('blur', blur);
        const text = el.textContent;
        if (!keep || text === before) { el.textContent = before; return; }
        try { await setText(e, text); say('Texto guardado.'); } catch (err) { el.textContent = before; say(err.message, true); }
      };
      const key = ev => {
        ev.stopPropagation();
        if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
        else if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); finish(true); }
      };
      const blur = () => finish(true);
      el.addEventListener('keydown', key); el.addEventListener('blur', blur);
    }

    // ---- selección, cajas e inspector ----
    const boards = new Map();  // path -> {fr}
    function rectOf(e, node) {
      const fr = boards.get(e.path)?.fr;
      if (!fr?.isConnected || !node.isConnected) return null;
      const f = fr.getBoundingClientRect(), v = vp.getBoundingClientRect(), k = f.width / fr.offsetWidth, r = node.getBoundingClientRect();
      return {x: f.left - v.left + r.left * k, y: f.top - v.top + r.top * k, w: r.width * k, h: r.height * k};
    }
    function box(e, node, cls, tag) {
      const r = rectOf(e, node);
      if (!r) return;
      const b = Object.assign(document.createElement('div'), {className: 'ce-box ' + cls});
      Object.assign(b.style, {left: r.x + 'px', top: r.y + 'px', width: r.w + 'px', height: r.h + 'px'});
      if (tag) b.append(Object.assign(document.createElement('span'), {className: 'ce-tag', textContent: tag}));
      layer.append(b);
    }
    const anchorFor = el => ({el: +el.dataset.dcTpl, tag: short(el), text: (el.textContent || '').trim().slice(0, 60)});
    const twins = e => [...e.el.ownerDocument.querySelectorAll(`[data-dc-tpl="${e.idx}"]`)];
    const short = el => el.localName + (el.classList[0] ? '.' + el.classList[0] : '');
    function redraw() {
      layer.replaceChildren();
      if (hover && hover.el !== sel?.el) box(hover, hover.el, 'hover');
      if (sel) {
        for (const t of twins(sel)) if (t !== sel.el) box(sel, t, 'twin');
        box(sel, sel.el, '', short(sel.el));
      }
    }
    function say(text, err = false) { msg = text; msgErr = err; const m = panel.querySelector('.ce-msg'); if (m) { m.textContent = text; m.classList.toggle('err', err); } }
    const toHex = c => {
      const m = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/.exec(c || '');
      if (!m || m[4] === '0') return null;
      return '#' + m.slice(1, 4).map(n => (+n | 0).toString(16).padStart(2, '0')).join('');
    };
    function drawPanel() {
      if (!sel) { panel.hidden = true; return; }
      const e = sel, i = info(e), cs = e.el.ownerDocument.defaultView.getComputedStyle(e.el);
      const crumbs = [];
      for (let n = e.el; n && n.localName !== 'body'; n = n.parentElement) if (n.dataset?.dcTpl) crumbs.unshift(short(n));
      const n = twins(e).length;
      panel.hidden = false;
      panel.innerHTML = `<header><div class="ce-path"><b></b><small></small></div><button type="button" data-a="close" aria-label="Cerrar">×</button></header>
        <div class="ce-sec"><h3>Texto</h3><div class="ce-txt"></div></div>
        <div class="ce-sec ce-style"><h3>Estilo</h3></div>
        <div class="ce-sec"><div class="ce-acts"><button type="button" data-a="comment">Comentar</button><button type="button" data-a="copy">Copiar referencia</button><button type="button" data-a="undo">Deshacer</button></div><div class="ce-msg"></div></div>`;
      panel.querySelector('b').textContent = `${boardTitle(e.path)} › ${short(e.el)}`;
      panel.querySelector('small').textContent = crumbs.slice(-4).join(' › ') + (i.t ? ` · línea ${i.line}` : '') + (n > 1 ? ` · se repite ${n} veces (cambia en todas)` : '');
      const txt = panel.querySelector('.ce-txt');
      if (i.span) {
        const b = Object.assign(document.createElement('button'), {type: 'button', textContent: 'Editar texto (doble clic)'});
        b.onclick = () => startTextEdit(e); txt.append(b);
      } else txt.textContent = !i.t ? i.why : e.el.children.length ? 'Tiene elementos adentro: seleccioná uno para editar su texto.' : 'Texto con datos ({{ }}) o marcado: no se edita acá.';
      const sty = panel.querySelector('.ce-style');
      if (!i.t || i.styleLocked) sty.append(Object.assign(document.createElement('div'), {textContent: !i.t ? 'Sin estilo editable.' : 'El estilo depende de datos ({{ }}): no se edita acá.'}));
      else for (const [prop, label, kind] of STYLES) {
        const own = i.st.decl.find(([k]) => k === prop)?.[1] ?? '';
        const row = Object.assign(document.createElement('label'), {className: 'ce-row'});
        const wrap = document.createElement('div');
        let input;
        if (kind === 'weight') {
          input = document.createElement('select');
          for (const [v, t] of [['', `— (${cs.fontWeight})`], ['400', 'Normal'], ['500', 'Media'], ['600', 'Semi'], ['700', 'Negrita'], ['800', 'Extra']]) input.append(new Option(t, v));
          input.value = own;
        } else {
          input = Object.assign(document.createElement('input'), {type: 'text', value: own, placeholder: cs.getPropertyValue(prop)});
          if (kind === 'color') {
            const pick = Object.assign(document.createElement('input'), {type: 'color', value: toHex(own || cs.getPropertyValue(prop)) || '#ffffff', title: label});
            pick.onchange = () => { input.value = pick.value; input.dispatchEvent(new Event('change')); };
            wrap.append(pick);
          }
        }
        input.onchange = () => setStyle(e, prop, input.value).then(() => say(`${label} guardado.`), err => say(err.message, true));
        input.onkeydown = ev => ev.stopPropagation();  // Espacio y atajos del lienzo no aplican al escribir
        wrap.append(input);
        row.append(Object.assign(document.createElement('span'), {textContent: label}), wrap);
        sty.append(row);
      }
      panel.querySelector('[data-a=undo]').disabled = !undo.length;
      panel.querySelector('[data-a=undo]').onclick = doUndo;
      panel.querySelector('[data-a=close]').onclick = () => select(null);
      panel.querySelector('[data-a=comment]').onclick = () => {
        const r = rectOf(e, e.el);
        if (!r) return say('El board se está recargando: probá de nuevo en un segundo.', true);
        comments.compose({kind: 'canvas', page: pageOf(), board: e.path, ...anchorFor(e.el),
          x: Math.round((r.x - view.x) / view.k), y: Math.round((r.y - view.y) / view.k)});
      };
      panel.querySelector('[data-a=copy]').onclick = () => {
        const ref = `Design ${art} · project/${e.path} · elemento data-dc-tpl="${e.idx}" <${short(e.el)}>` +
          (i.t ? ` (línea ${i.line} del fuente)` : '') + ` · texto: "${(e.el.textContent || '').trim().slice(0, 80)}"`;
        navigator.clipboard.writeText(ref).then(() => say('Referencia copiada: pegala en una sesión de Claude.'), () => say(ref));
      };
      say(msg, msgErr);
    }
    async function select(e) {
      if (editing) editing.blur();
      sel = e; msg = ''; hover = null;
      if (e) { try { await load(e.path); } catch (err) { msg = err.message; msgErr = true; } }
      drawPanel(); redraw();
    }
    function reloadBoard(path) {
      const b = boards.get(path);
      if (!b) return;
      const was = sel, keep = sel?.path === path ? sel.idx : null;
      b.fr.addEventListener('load', () => {  // el board vuelve a renderizar: reubicar la selección por su número
        if (keep == null) return redraw();
        let tries = 30;  // ~3 s: si el elemento ya no existe, se suelta la selección
        const t = () => {
          if (sel !== was) return;  // mientras recargaba se eligió otra cosa
          const el = b.fr.contentDocument?.querySelector(`[data-dc-tpl="${keep}"]`);
          if (el) { sel = {path, idx: keep, el}; drawPanel(); redraw(); }
          else if (--tries) setTimeout(t, 100);
          else select(null);
        };
        t();
      }, {once: true});
      b.fr.contentWindow.location.reload();
    }

    // ---- lo que el lienzo le pasa ----
    return {
      hook(fr, path) {  // al cargar cada board
        boards.set(path, {fr});
        const w = fr.contentWindow, d = fr.contentDocument;
        const target = n => n?.closest?.('[data-dc-tpl]');
        w.addEventListener('pointermove', ev => {
          const t = target(ev.target);
          if (!t || ev.buttons) { if (hover) { hover = null; redraw(); } return; }
          if (hover?.el !== t) { hover = {path, idx: +t.dataset.dcTpl, el: t}; redraw(); }
        });
        d.addEventListener('pointerleave', () => { if (hover) { hover = null; redraw(); } });
        for (const ev of ['click', 'auxclick', 'submit']) w.addEventListener(ev, e => {  // seleccionar no activa links ni botones del board;
          if (!e.altKey && !editing?.contains(e.target)) { e.preventDefault(); e.stopPropagation(); }  // con ⌥ el board se usa normal
        }, true);
        w.addEventListener('dblclick', ev => {
          const t = target(ev.target);
          if (t && sel?.el === t) { ev.preventDefault(); startTextEdit(sel); }
        }, true);
        w.addEventListener('keydown', ev => {
          if (editing || ev.target.closest?.('input,textarea,select,[contenteditable]')) return;
          if (ev.key === 'Escape') select(null);
          else if ((ev.metaKey || ev.ctrlKey) && ev.key === 'z') { ev.preventDefault(); doUndo(); }
        });
      },
      pick(path, node, alt) {  // clic izquierdo dentro de un board; con ⌥ o editando texto, el clic es del board
        if (alt || editing?.contains(node)) return;
        const t = node?.closest?.('[data-dc-tpl]');
        select(t ? {path, idx: +t.dataset.dcTpl, el: t} : null);
      },
      anchorFor,
      key(ev) {
        if (editing || ev.target.closest?.('input,textarea,select,[contenteditable]')) return;
        if (ev.key === 'Escape' && sel) select(null);
        else if ((ev.metaKey || ev.ctrlKey) && ev.key === 'z' && undo.length) { ev.preventDefault(); doUndo(); }
      },
      clear: () => select(null),
      redraw,
      label: a => `${boardTitle(a.board)} › ${a.tag}${a.text ? ` «${a.text}»` : ''}`,
    };
  },
};
