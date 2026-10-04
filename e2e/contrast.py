"""WCAG contrast measurement for the page, run inside the browser on the colours it really computed.

audit(page) returns the visible text whose contrast against its background is below the WCAG AA minimum
(4.5:1, or 3:1 for large text: 24 px, or 18.66 px and bold). Backgrounds are composited from the element's own
colour up through its ancestors (translucent layers are blended); where the background is an image or gradient the
element is skipped, because no honest single number exists. Text drawn on the map tiles is reported as "approx":
the tile behind it can be any colour, so it is measured against the white or dark glass layer it sits on."""

AUDIT_JS = r"""
() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  const rgba = (css) => { cx.clearRect(0,0,1,1); cx.fillStyle = '#000'; cx.fillStyle = css; cx.fillRect(0,0,1,1);
                          const d = cx.getImageData(0,0,1,1).data; return d[3] === 0 ? [0,0,0,0] : [d[0], d[1], d[2], d[3]/255]; };
  const lum = ([r,g,b]) => { const f = v => { v /= 255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); }; return .2126*f(r) + .7152*f(g) + .0722*f(b); };
  const over = (fg, bg) => [0,1,2].map(i => fg[i]*fg[3] + bg[i]*(1-fg[3]));
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const hex = (c) => '#' + c.map(v => Math.round(v).toString(16).padStart(2,'0')).join('');
  const out = [], seen = new Set();
  const visible = (e) => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && cs.visibility !== 'hidden' && cs.display !== 'none' && !e.closest('.sr,[hidden]'); };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.trim(); if (!text) continue;
    const e = node.parentElement; if (!e || !visible(e) || e.closest('script,style,noscript,option,select')) continue;
    if (e.closest(':disabled') || e.closest('[aria-hidden=true]')) continue;
    const isSvg = e instanceof SVGElement;
    const cs = getComputedStyle(e);
    // opacity of the element and its ancestors
    let op = 1; for (let a = e; a && a !== document.documentElement; a = a.parentElement) op *= parseFloat(getComputedStyle(a).opacity);
    if (op < .05) continue;
    let fg = rgba(isSvg ? cs.fill : cs.color);
    // background: walk up, blending translucent layers
    let layers = [], approx = false, unknown = false, a = e;
    for (; a && a !== document.documentElement; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.backgroundImage !== 'none' && !s.backgroundImage.startsWith('none')) { if (!a.matches('body')) { unknown = true; break; } }
      const bg = rgba(s.backgroundColor);
      if (bg[3] > 0) { layers.push(bg); if (bg[3] >= .999) break; }
      if (a.classList.contains('leaflet-container') || a.id === 'map') { approx = true; break; }
    }
    if (unknown) continue;
    let base = a && layers.length && layers[layers.length-1][3] >= .999 ? layers.pop().slice(0,3) : (dark ? [10,23,29] : [243,246,248]);
    if (!layers.length && !(a && a.id !== 'map') ) approx = approx || false;
    for (let i = layers.length-1; i >= 0; i--) base = over(layers[i], base);
    const eff = [fg[0], fg[1], fg[2], fg[3]*op];
    const col = over(eff, base);
    const L1 = lum(col), L2 = lum(base), ratio = (Math.max(L1,L2)+.05)/(Math.min(L1,L2)+.05);
    const px = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const large = px >= 24 || (px >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    if (ratio + .005 < need) {
      const key = (e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className) + '|' + hex(col) + '|' + hex(base);
      if (seen.has(key)) continue; seen.add(key);
      out.push({text: text.slice(0, 40), ratio: Math.round(ratio*100)/100, need, fg: hex(col), bg: hex(base), px, bold,
                el: e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + '.' + String(e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className).trim().split(/\s+/).slice(0,2).join('.'),
                approx});
    }
  }
  return out;
}
"""


def audit(page):
    return page.evaluate(AUDIT_JS)
