// CommerceCrew live API client for the demo path: draft a listing, approve or reject it, read agent events.
// The service serves this page, so routes are same-origin by default; set window.CC_API_BASE to point elsewhere.
(function () {
  const base = () => (window.CC_API_BASE || '').replace(/\/$/, '');

  // FastAPI errors carry `detail` as a string, or as a list of validation errors.
  function detailText(body, status) {
    const d = body && body.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(x => (x && x.msg) || String(x)).join('; ');
    return 'The service answered ' + status + '.';
  }

  async function call(method, path, payload) {
    let res;
    try {
      res = await fetch(base() + path, {
        method,
        headers: payload ? { 'Content-Type': 'application/json' } : {},
        body: payload ? JSON.stringify(payload) : undefined,
      });
    } catch (e) {
      throw new Error('Could not reach the CommerceCrew service. Check the connection and try again.');
    }
    let body = null;
    try { body = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error(detailText(body, res.status));
    return body;
  }

  // Any image src (data URL or same-origin asset) as a JPEG data URL of at most 1200 px on the long side.
  function toDataUrl(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onerror = () => reject(new Error('Could not read the photo.'));
      img.onload = () => {
        const k = Math.min(1, 1200 / Math.max(img.width, img.height));
        const cv = document.createElement('canvas');
        cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k);
        cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
        resolve(cv.toDataURL('image/jpeg', 0.85));
      };
      img.src = src;
    });
  }

  window.CCApi = {
    // POST /api/products/draft. Resolves with the WorkflowView; `error` may be set on a 201.
    draft: (photo, notes, productId) => call('POST', '/api/products/draft', { photo: photo || null, notes: notes || '', productId: productId || null }),
    // POST /api/products/{productId}/approval. `price` (a string such as "8500.00" or "0.00") is sent
    // only on an approval; it replaces any price from the notes.
    decide: (productId, approved, price) => call('POST', '/api/products/' + encodeURIComponent(productId) + '/approval',
      approved && price ? { approved: true, price } : { approved: !!approved }),
    // GET /api/events: every workflow's action log, oldest first.
    events: () => call('GET', '/api/events'),
    toDataUrl,
  };
})();
