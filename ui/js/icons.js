// Lucide outline icons (subset) as a tiny <cc-icon name="" size=""> web component.
(function () {
  const p = d => `<path d="${d}"/>`;
  const c = (x, y, r) => `<circle cx="${x}" cy="${y}" r="${r}"/>`;
  const r = (x, y, w, h, rx) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}"/>`;
  const I = {
    'layout-dashboard': r(3,3,7,9,1)+r(14,3,7,5,1)+r(14,12,7,9,1)+r(3,16,7,5,1),
    'package': p('M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z')+p('M12 22V12')+p('m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7')+p('m7.5 4.27 9 5.15'),
    'list-checks': p('m3 17 2 2 4-4')+p('m3 7 2 2 4-4')+p('M13 6h8')+p('M13 12h8')+p('M13 18h8'),
    'message-square': p('M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'),
    'activity': p('M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2'),
    'plus': p('M5 12h14')+p('M12 5v14'),
    'search': c(11,11,8)+p('m21 21-4.3-4.3'),
    'chevron-down': p('m6 9 6 6 6-6'),
    'chevron-up': p('m18 15-6-6-6 6'),
    'chevron-right': p('m9 18 6-6-6-6'),
    'chevron-left': p('m15 18-6-6 6-6'),
    'chevrons-up-down': p('m7 15 5 5 5-5')+p('m7 9 5-5 5 5'),
    'x': p('M18 6 6 18')+p('m6 6 12 12'),
    'check': p('M20 6 9 17l-5-5'),
    'upload': p('M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4')+p('m17 8-5-5-5 5')+p('M12 3v12'),
    'image': r(3,3,18,18,2)+c(9,9,2)+p('m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21'),
    'maximize': p('M15 3h6v6')+p('M9 21H3v-6')+p('M21 3l-7 7')+p('M3 21l7-7'),
    'star': p('M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z'),
    'trash': p('M3 6h18')+p('M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6')+p('M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2'),
    'more': c(12,12,1)+c(19,12,1)+c(5,12,1),
    'rotate-ccw': p('M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8')+p('M3 3v5h5'),
    'refresh': p('M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8')+p('M21 3v5h-5')+p('M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16')+p('M8 16H3v5'),
    'eye': p('M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0')+c(12,12,3),
    'user': p('M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2')+c(12,7,4),
    'circle-dashed': p('M10.1 2.182a10 10 0 0 1 3.8 0')+p('M13.9 21.818a10 10 0 0 1-3.8 0')+p('M17.609 3.721a10 10 0 0 1 2.69 2.7')+p('M2.182 13.9a10 10 0 0 1 0-3.8')+p('M20.279 17.609a10 10 0 0 1-2.7 2.69')+p('M21.818 10.1a10 10 0 0 1 0 3.8')+p('M3.721 6.391a10 10 0 0 1 2.7-2.69')+p('M6.391 20.279a10 10 0 0 1-2.69-2.7'),
    'megaphone': p('m3 11 18-5v12L3 14v-3z')+p('M11.6 16.8a3 3 0 1 1-5.8-1.6'),
    'send': p('M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z')+p('m21.854 2.147-10.94 10.939'),
    'alert-circle': c(12,12,10)+p('M12 8v4')+p('M12 16h.01'),
    'info': c(12,12,10)+p('M12 16v-4')+p('M12 8h.01'),
    'check-circle': c(12,12,10)+p('m9 12 2 2 4-4'),
    'x-circle': c(12,12,10)+p('m15 9-6 6')+p('m9 9 6 6'),
    'clock': c(12,12,10)+p('M12 6v6l4 2'),
    'route': c(6,19,3)+p('M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15')+c(18,5,3),
    'shopping-bag': p('M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z')+p('M3 6h18')+p('M16 10a4 4 0 0 1-8 0'),
    'bar-chart': p('M3 3v18h18')+p('M18 17V9')+p('M13 17V5')+p('M8 17v-3'),
    'briefcase': p('M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16')+r(2,6,20,14,2),
    'tag': p('M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z')+c(7.5,7.5,.5),
    'layers': p('M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z')+p('m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65')+p('m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65'),
    'archive': r(2,3,20,5,1)+p('M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8')+p('M10 12h4'),
    'square': r(3,3,18,18,2),
    'watch': c(12,12,6)+p('M12 10v2l1 1')+p('m16.13 7.66-.81-4.05a2 2 0 0 0-2-1.61h-2.68a2 2 0 0 0-1.96 1.61l-.81 4.05')+p('m7.88 16.36.8 4a2 2 0 0 0 1.96 1.61h2.72a2 2 0 0 0 1.98-1.61l.81-4.05'),
    'plug': p('M12 22v-5')+p('M9 8V2')+p('M15 8V2')+p('M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z'),
    'pencil': p('M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z'),
    'file-text': p('M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z')+p('M14 2v4a2 2 0 0 0 2 2h4')+p('M10 9H8')+p('M16 13H8')+p('M16 17H8'),
    'arrow-right': p('M5 12h14')+p('m12 5 7 7-7 7'),
    'arrow-left': p('m12 19-7-7 7-7')+p('M19 12H5'),
    'loader': p('M21 12a9 9 0 1 1-6.219-8.56'),
    'store': p('m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7')+p('M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8')+p('M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4')+p('M2 7h20'),
    'mail': r(2,4,20,16,2)+p('m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7'),
    'corner-down-right': p('m15 10 5 5-5 5')+p('M4 4v7a4 4 0 0 0 4 4h12'),
    'log-out': p('m16 17 5-5-5-5')+p('M21 12H9')+p('M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'),
    'inbox': p('M22 12h-6l-2 3h-4l-2-3H2')+p('M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z'),
  };
  class CCIcon extends HTMLElement {
    static get observedAttributes() { return ['name', 'size', 'stroke']; }
    connectedCallback() { this.render(); }
    attributeChangedCallback() { this.render(); }
    render() {
      const s = this.getAttribute('size') || 16, w = this.getAttribute('stroke') || 1.75;
      this.style.display = 'inline-flex'; this.style.flex = 'none'; this.style.lineHeight = '0';
      this.setAttribute('aria-hidden', 'true');
      // Draw into a shadow root, never light DOM: dc-runtime reads its first template from the live
      // page, and React owns this element's children, so writing innerHTML here breaks both.
      const root = this.shadowRoot || this.attachShadow({ mode: 'open' });
      root.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round">${I[this.getAttribute('name')] || ''}</svg>`;
    }
  }
  if (!customElements.get('cc-icon')) customElements.define('cc-icon', CCIcon);
})();
