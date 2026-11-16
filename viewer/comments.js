// Comentarios locales para el visor: panel, modo "Comentar" y pines. Se guardan en <id>/_comments/ por
// /_api/comments/<id>; no se publican a claude.ai. Cada vista (lienzo, presentación) aporta cómo
// convertir un clic en un ancla, dónde dibujar el pin de un ancla y cómo llevar la vista hasta él.
window.LocalComments = {
  mount({art, header, pickTarget, anchorAt, pinFor, focus, label, onPickMode}) {
    const API = `/_api/comments/${art}`;
    const css = document.createElement('style');
    css.textContent = `
      .lc-btn[aria-pressed=true]{background:var(--acc);color:var(--bar);border-color:var(--acc)}
      .lc-panel{position:fixed;top:48px;right:0;bottom:0;width:min(340px,100%);background:var(--bar);color:var(--fg);
        border-left:1px solid var(--line);z-index:20;display:flex;flex-direction:column;font:14px/1.45 system-ui,sans-serif}
      .lc-panel[hidden]{display:none}
      .lc-head{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--line)}
      .lc-head h2{font-size:15px;margin:0}
      .lc-list{flex:1;overflow:auto;padding:8px 0;margin:0;list-style:none}
      .lc-item{padding:10px 16px;border-bottom:1px solid var(--line);display:grid;gap:4px}
      .lc-item.done{opacity:.55}
      .lc-meta{display:flex;gap:8px;align-items:center;color:var(--mut);font-size:12px}
      .lc-num{display:inline-grid;place-items:center;min-width:20px;height:20px;border-radius:10px;background:var(--acc);color:var(--bar);font-size:11px;font-weight:700}
      .lc-text{white-space:pre-wrap;word-break:break-word}
      .lc-acts{display:flex;gap:6px;flex-wrap:wrap}
      .lc-acts button,.lc-form button{font:inherit;font-size:12px;color:inherit;background:transparent;border:1px solid var(--line);border-radius:6px;padding:3px 8px;cursor:pointer}
      .lc-acts button:focus-visible,.lc-form button:focus-visible,.lc-form textarea:focus-visible{outline:2px solid var(--acc)}
      .lc-form{padding:12px 16px;border-bottom:1px solid var(--line);display:grid;gap:8px}
      .lc-form textarea{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px;min-height:72px;resize:vertical}
      .lc-empty{padding:16px;color:var(--mut)}
      .lc-layer{position:absolute;inset:0;pointer-events:none;z-index:5}
      .lc-pin{position:absolute;transform:translate(-50%,-100%);pointer-events:auto;cursor:pointer;min-width:24px;height:24px;
        padding:0 6px;border-radius:12px 12px 12px 2px;background:var(--acc);color:var(--bar);border:2px solid var(--bar);
        font:700 12px/20px system-ui,sans-serif;text-align:center;box-shadow:0 1px 4px #0006}
      .lc-pin.done{opacity:.5}
      .lc-picking,.lc-picking *{cursor:crosshair!important}
      .lc-picking iframe{pointer-events:none}`;
    document.head.append(css);

    const btn = (text, attrs = {}) => Object.assign(document.createElement('button'), {type: 'button', textContent: text}, attrs);
    const toggle = btn('Comentarios', {className: 'lc-btn', ariaPressed: 'false'});
    const pick = btn('Comentar', {className: 'lc-btn', ariaPressed: 'false', title: 'Hacé clic donde querés dejar el comentario'});
    header.append(pick, toggle);
    const panel = Object.assign(document.createElement('aside'), {className: 'lc-panel', hidden: true});
    panel.innerHTML = `<div class="lc-head"><h2>Comentarios locales</h2></div><div class="lc-form" hidden></div><ul class="lc-list"></ul>`;
    document.body.append(panel);
    const form = panel.querySelector('.lc-form'), list = panel.querySelector('.lc-list');
    let rows = [], picking = false, draft = null, rev = -1;

    const call = async (method, cid, data) => {
      const r = await fetch(API + (cid ? `?cid=${encodeURIComponent(cid)}` : ''), {method,
        headers: data ? {'Content-Type': 'application/json'} : {}, body: data && JSON.stringify({data})});
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    };
    const open = show => { panel.hidden = !show; toggle.setAttribute('aria-pressed', String(show)); };
    const setPick = on => {
      picking = on; pick.setAttribute('aria-pressed', String(on));
      pickTarget.classList.toggle('lc-picking', on); onPickMode?.(on);
    };

    function drawPins() {
      document.querySelectorAll('.lc-pin').forEach(p => p.remove());
      rows.forEach((c, i) => {
        const at = pinFor(c.anchor);
        if (!at) return;
        const pin = Object.assign(document.createElement('button'), {type: 'button', className: 'lc-pin' + (c.resolved ? ' done' : ''),
          textContent: i + 1, title: String(c.text ?? '').slice(0, 80)});
        pin.style.left = at.x + 'px'; pin.style.top = at.y + 'px';
        pin.onclick = e => { e.stopPropagation(); open(true); list.children[i]?.scrollIntoView({block: 'nearest'}); };
        at.layer.append(pin);
      });
    }
    function drawList() {
      toggle.textContent = `Comentarios (${rows.filter(c => !c.resolved).length})`;
      list.replaceChildren();
      if (!rows.length) { list.innerHTML = '<li class="lc-empty">Todavía no hay comentarios. Usá «Comentar» y hacé clic sobre la obra.</li>'; return; }
      rows.forEach((c, i) => {
        const li = Object.assign(document.createElement('li'), {className: 'lc-item' + (c.resolved ? ' done' : '')});
        const meta = Object.assign(document.createElement('div'), {className: 'lc-meta'});
        meta.append(Object.assign(document.createElement('span'), {className: 'lc-num', textContent: i + 1}),
          document.createTextNode(`${c.author} · ${new Date(c.at).toLocaleString()} · ${label(c.anchor)}`));
        const text = Object.assign(document.createElement('div'), {className: 'lc-text', textContent: String(c.text ?? '')});
        const acts = Object.assign(document.createElement('div'), {className: 'lc-acts'});
        const go = btn('Ver'); go.onclick = () => focus(c.anchor);
        const res = btn(c.resolved ? 'Reabrir' : 'Resolver');
        res.onclick = () => call('PATCH', c.id, {resolved: !c.resolved}).then(load);
        const del = btn('Borrar');
        del.onclick = () => {  // confirmación dentro de la página: sin diálogos del navegador
          acts.replaceChildren(document.createTextNode('¿Borrar este comentario? '));
          const yes = btn('Sí, borrar'), no = btn('No');
          yes.onclick = () => call('DELETE', c.id).then(load); no.onclick = drawList;
          acts.append(yes, no); no.focus();
        };
        acts.append(go, res, del);
        li.append(meta, text, acts); list.append(li);
      });
    }
    function compose(anchor) {
      draft = anchor; open(true);
      form.hidden = false;
      form.innerHTML = `<label for="lc-text">Nuevo comentario en ${label(anchor).replace(/[<&>]/g, '')}</label>
        <textarea id="lc-text" maxlength="4000"></textarea>
        <div class="lc-acts"><button type="button" data-a="save">Guardar</button><button type="button" data-a="cancel">Cancelar</button></div>`;
      const ta = form.querySelector('textarea'); ta.focus();
      form.querySelector('[data-a=cancel]').onclick = () => { form.hidden = true; draft = null; };
      form.querySelector('[data-a=save]').onclick = async () => {
        if (!ta.value.trim()) { ta.focus(); return; }
        await call('POST', null, {text: ta.value, anchor: draft});
        form.hidden = true; draft = null; load();
      };
    }
    async function load() {
      try { rows = await call('GET'); } catch { rows = []; }
      drawList(); drawPins();
    }

    toggle.onclick = () => open(panel.hidden);
    pick.onclick = () => setPick(!picking);
    pickTarget.addEventListener('click', e => {
      if (!picking) return;
      const a = anchorAt(e);
      if (!a) return;
      e.preventDefault(); e.stopPropagation(); setPick(false); compose(a);
    }, true);
    addEventListener('keydown', e => { if (e.key === 'Escape' && picking) setPick(false); });
    setInterval(async () => {  // otra pestaña comentó: el contador de revisión del servidor cambia
      try { const r = await (await fetch(`/_api/rev/${art}?path=`)).json(); if (r.rev !== rev) { rev = r.rev; load(); } } catch {}
    }, 3000);
    load();
    return {redraw: drawPins, compose};
  },
};
