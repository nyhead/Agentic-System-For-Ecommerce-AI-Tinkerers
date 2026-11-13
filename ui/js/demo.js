// CommerceCrew demo data + simulated agent services.
// Presentation reads only from window.CCDemo; swap `agents.*` for real API calls later.
(function () {
  const VERSION = 4;
  const MIN = 60000;
  const ago = m => Date.now() - m * MIN;
  let seq = 0;
  const uid = p => p + '-' + Date.now().toString(36) + (seq++).toString(36);
  const wait = ms => new Promise(r => setTimeout(r, ms * ((window.CCDemo && window.CCDemo.speed) || 1)));
  const euro = n => '€' + Number(n).toLocaleString('en-IE');

  const AGENTS = {
    product:   { name: 'Product agent',   icon: 'package' },
    research:  { name: 'Research agent',  icon: 'search' },
    support:   { name: 'Support agent',   icon: 'message-square' },
    triage:    { name: 'Triage agent',    icon: 'route' },
    marketing: { name: 'Marketing agent', icon: 'megaphone' },
    sales:     { name: 'Sales agent',     icon: 'shopping-bag' },
    analytics: { name: 'Analytics agent', icon: 'bar-chart' },
  };

  // One watch, six photos. fx/fy = crop focus (%), zoom = thumbnail crop scale.
  const WATCH_GALLERY = () => [
    { id: 'w-cover',    src: 'assets/watch-cover.jpg',    role: 'Cover',   label: 'Front, in presentation box', fx: 53, fy: 49, zoom: 2.0 },
    { id: 'w-dial',     src: 'assets/watch-dial.jpg',     role: 'Detail',  label: 'Dial close-up, angled',      fx: 49, fy: 47, zoom: 1.4 },
    { id: 'w-front',    src: 'assets/watch-front.jpg',    role: 'Gallery', label: 'Full front',                 fx: 58, fy: 40, zoom: 2.6 },
    { id: 'w-reverse',  src: 'assets/watch-reverse.jpg',  role: 'Gallery', label: 'Full reverse',               fx: 62, fy: 53, zoom: 2.4 },
    { id: 'w-caseback', src: 'assets/watch-caseback.jpg', role: 'Detail',  label: 'Case back close-up',         fx: 53, fy: 44, zoom: 1.5 },
    { id: 'w-strap',    src: 'assets/watch-strap.jpg',    role: 'Detail',  label: 'Strap marking close-up',     fx: 46, fy: 52, zoom: 1.5 },
  ];

  const WATCH_BASE_DESC = 'Omega Seamaster De Ville automatic wristwatch with a silver-tone textured dial, black Arabic numerals and baton hands, in a polished steel-tone case. The dial reads OMEGA, AUTOMATIC, Seamaster DE VILLE and SWISS MADE. The case back carries the Seamaster medallion marked WATERPROOF and a personal presentation engraving. Fitted with a brown leather strap stamped “Top grain leather · Hand made” and a steel-tone pin buckle.';
  const WATCH_CLAIMS = () => [
    { id: 'cl-water', text: 'Water resistant for everyday wear.', why: 'Inferred from the “WATERPROOF” case-back marking. On a vintage watch this depends on its seals and a recent pressure test.', state: 'open' },
    { id: 'cl-year', text: 'Made in 1960.', why: 'Taken from the engraved “1960–1972”. Engraved dates usually mark a presentation, not when the watch was made.', state: 'open' },
  ];

  // Simulated Research agent output (synthetic). A real backend compares live listings and cites them.
  const WATCH_RESEARCH = () => ({ suggested: 850, low: 690, high: 1100, comps: 6, basis: 'sold listings of 1960s Seamaster De Ville automatics', confidence: 'Medium' });
  const GENERIC_RESEARCH = () => ({ suggested: 35, low: 25, high: 50, comps: 3, basis: 'listings that look similar to your photos', confidence: 'Low' });

  function watchDraft(over) {
    const claims = WATCH_CLAIMS();
    const research = WATCH_RESEARCH();
    return Object.assign({
      id: uid('p'), name: 'Omega Seamaster De Ville — brown strap', nameSrc: 'ai',
      nameNote: 'From visible dial text · awaiting your confirmation',
      category: 'Watches', categoryKey: 'watch', sku: '', status: 'needs_review',
      price: research.suggested, priceSrc: 'research', research, currency: 'EUR', stock: 1, stockSrc: 'default',
      condition: '', working: '', box: '',
      optional: { reference: '', year: '', diameter: '', calibre: '' },
      description: WATCH_BASE_DESC + ' ' + claims.map(c => c.text).join(' '), descSrc: 'ai',
      claims,
      observations: [
        { id: 'o1', area: 'Dial', text: 'OMEGA · AUTOMATIC · Seamaster DE VILLE · SWISS MADE', img: 'w-dial' },
        { id: 'o2', area: 'Case back', text: 'Seamaster medallion, marked “WATERPROOF”', img: 'w-caseback', note: 'A marking, not a tested rating' },
        { id: 'o3', area: 'Engraving', text: '“1960–1972 · PLYMOUTH L.M.C.” with a personal name', img: 'w-caseback', note: 'Presentation engraving — not proof of manufacture year' },
        { id: 'o4', area: 'Strap', text: '“TOP GRAIN LEATHER · HAND MADE”', img: 'w-strap' },
        { id: 'o5', area: 'Strap tip', text: 'Stamped “17”', img: 'w-reverse', note: 'Possibly a size — not verified' },
      ],
      details: [
        { k: 'Brand', v: 'Omega', src: 'seen' },
        { k: 'Model line', v: 'Seamaster De Ville', src: 'seen' },
        { k: 'Movement', v: 'Automatic, per dial text', src: 'seen' },
        { k: 'Dial', v: 'Silver-tone, Arabic numerals', src: 'ai' },
        { k: 'Case', v: 'Round, polished steel-tone', src: 'ai' },
        { k: 'Strap', v: 'Brown leather, pin buckle', src: 'seen' },
        { k: 'Category', v: 'Watches › Wristwatches', src: 'ai' },
      ],
      images: WATCH_GALLERY(), coverId: 'w-cover', icon: 'watch',
      history: [], updated: Date.now(), created: Date.now(),
    }, over || {});
  }

  function simpleProduct(o) {
    return Object.assign({
      nameSrc: 'you', descSrc: 'you', priceSrc: 'you', stockSrc: 'you', currency: 'EUR', condition: 'New', working: '', box: '',
      optional: { reference: '', year: '', diameter: '', calibre: '' },
      claims: [], observations: [], details: [], images: [], coverId: null, history: [],
    }, o);
  }

  function seed() {
    const omega = watchDraft({ id: 'p-omega', updated: ago(18), created: ago(19) });
    omega.history = [
      { ts: ago(19), who: 'You', text: 'Uploaded 6 photos' },
      { ts: ago(18), who: 'Product agent', text: 'Prepared listing draft and grouped photos as one product' },
      { ts: ago(18), who: 'Research agent', text: 'Suggested ' + euro(omega.research.suggested) + ' from ' + omega.research.comps + ' comparable sold listings (synthetic)' },
      { ts: ago(18), who: 'Product agent', text: 'Set quantity to 1 and requested two claim checks' },
    ];
    const products = [
      omega,
      simpleProduct({ id: 'p-roll', name: 'Atelier Travel Watch Roll — tan leather', category: 'Accessories', categoryKey: 'accessory', sku: 'AT-ACC-0107', stock: 14, price: 89, status: 'published', icon: 'briefcase', updated: ago(60 * 26), created: ago(60 * 24 * 12),
        description: 'Demo product. A soft tan leather roll that holds two watches for travel, with a snap closure.', details: [{ k: 'Material', v: 'Leather (demo)', src: 'you' }, { k: 'Holds', v: '2 watches', src: 'you' }],
        history: [{ ts: ago(60 * 24 * 12), who: 'You', text: 'Created product' }, { ts: ago(60 * 26), who: 'You', text: 'Published in demo' }] }),
      simpleProduct({ id: 'p-strap', name: 'Replacement Strap 18 mm — chestnut calf', category: 'Straps', categoryKey: 'strap', sku: 'AT-STR-0218', stock: 32, price: 45, status: 'published', icon: 'tag', updated: ago(60 * 50), created: ago(60 * 24 * 20),
        description: 'Demo product. Chestnut calf leather strap, 18 mm lug width, with quick-release spring bars.', details: [{ k: 'Lug width', v: '18 mm', src: 'you' }, { k: 'Material', v: 'Calf leather (demo)', src: 'you' }],
        history: [{ ts: ago(60 * 24 * 20), who: 'You', text: 'Created product' }] }),
      simpleProduct({ id: 'p-stand', name: 'Walnut Watch Stand — single', category: 'Accessories', categoryKey: 'accessory', sku: 'AT-ACC-0112', stock: 9, price: 38, status: 'published', icon: 'layers', updated: ago(60 * 74), created: ago(60 * 24 * 30),
        description: 'Demo product. A single-watch display stand in oiled walnut.', details: [{ k: 'Material', v: 'Walnut (demo)', src: 'you' }],
        history: [{ ts: ago(60 * 24 * 30), who: 'You', text: 'Created product' }] }),
      simpleProduct({ id: 'p-box', name: 'Three-slot Watch Box — natural linen', category: 'Accessories', categoryKey: 'accessory', sku: 'AT-ACC-0120', stock: 0, price: 120, status: 'published', icon: 'archive', updated: ago(60 * 24 * 4), created: ago(60 * 24 * 40),
        description: 'Demo product. Linen-covered box with three cushioned slots.', details: [{ k: 'Slots', v: '3', src: 'you' }],
        history: [{ ts: ago(60 * 24 * 40), who: 'You', text: 'Created product' }] }),
      simpleProduct({ id: 'p-cloth', name: 'Microfibre Polishing Cloth — set of 3', category: 'Accessories', categoryKey: 'accessory', sku: 'AT-ACC-0131', stock: 60, price: '', status: 'draft', icon: 'square', updated: ago(60 * 22), created: ago(60 * 22),
        description: 'Demo product. Set of three lint-free microfibre cloths for cases and crystals.',
        history: [{ ts: ago(60 * 22), who: 'You', text: 'Saved as draft' }] }),
    ];
    const decisions = [
      { id: 'd-listing', kind: 'listing', agent: 'product', productId: 'p-omega', title: 'Confirm watch details', action: 'Confirm details', status: 'pending', created: ago(18) },
      { id: 'd-support', kind: 'support', agent: 'support', productId: 'p-omega', convoId: 'c1', title: 'Customer asked about water resistance', text: 'The Support agent answered Hannah W. from the listing but couldn’t confirm a pressure test. Tell it what’s been tested so it can finish the reply.', action: 'Provide answer', status: 'pending', created: ago(11) },
      { id: 'd-campaign', kind: 'campaign', agent: 'marketing', productId: 'p-omega', title: 'Campaign draft ready', text: 'Instagram post for the Seamaster De Ville using the dial close-up. Review the copy and image.', action: 'Review draft', status: 'pending', created: ago(6) },
    ];
    const activity = [
      { id: 'a7', agent: 'analytics', title: 'Compiling weekly sales summary', desc: 'Reading 7 days of demo orders.', status: 'working', ts: ago(3), details: ['Source: demo order history (simulated)', 'Summary will appear here when finished'] },
      { id: 'a6', agent: 'marketing', title: 'Prepared campaign copy', desc: 'Instagram post for the Seamaster De Ville, awaiting your approval.', status: 'needs_input', ts: ago(6), decisionId: 'd-campaign', target: { type: 'decision', id: 'd-campaign', label: 'Campaign draft' }, next: 'Review draft',
        details: ['Selected the dial close-up as the lead image', 'Left year and water resistance out of the copy', 'Handed to you for approval — nothing is posted'] },
      { id: 'a5', agent: 'support', title: 'Answered first, then asked you', desc: 'Replied to Hannah W. from the listing but couldn’t verify water resistance. Needs your answer to finish.', status: 'needs_input', ts: ago(11), decisionId: 'd-support', target: { type: 'conversation', id: 'c1', label: 'Hannah Whitfield' }, next: 'Provide answer',
        details: ['Checked the listing, photos and store policies', 'Sent Hannah what the listing shows and said the shop is checking', 'The case-back “WATERPROOF” marking isn’t treated as a tested rating', 'Drafted a follow-up that waits for your answer'] },
      { id: 'a4', agent: 'triage', title: 'Routed a product question to support', desc: 'Hannah W.’s chat message matched a product question about the Seamaster.', status: 'completed', ts: ago(12), target: { type: 'conversation', id: 'c1', label: 'Hannah Whitfield' },
        details: ['Channel: store chat (simulated)', 'Linked to Omega Seamaster De Ville', 'Handed off to Support agent'] },
      { id: 'a3', agent: 'sales', title: 'Checking abandoned carts', desc: 'Two carts with the walnut stand from the last 24 hours.', status: 'working', ts: ago(15), target: { type: 'product', id: 'p-stand', label: 'Walnut Watch Stand' }, details: ['Will draft reminder emails for your approval'] },
      { id: 'a2', agent: 'product', title: 'Prepared a listing', desc: 'Drafted the Omega Seamaster De Ville from 6 photos.', status: 'completed', ts: ago(18), target: { type: 'product', id: 'p-omega', label: 'Omega Seamaster De Ville' },
        details: ['Grouped 6 photos as one product; chose the boxed front view as cover', 'Transcribed dial, case-back and strap markings', 'Set quantity to 1 — change it if you have more', 'Left year, reference, size, authenticity and condition for you'] },
      { id: 'a2r', agent: 'research', title: 'Researched a market price', desc: 'Suggested ' + euro(850) + ' for the Omega Seamaster De Ville from 6 comparable sold listings.', status: 'completed', ts: ago(17.6), target: { type: 'product', id: 'p-omega', label: 'Omega Seamaster De Ville' },
        details: ['Range ' + euro(690) + '–' + euro(1100) + ' across 6 listings · synthetic in this demo', 'Confidence: medium — condition and service history move the price', 'Shown to you as a suggestion; nothing publishes until you approve'] },
      { id: 'a1', agent: 'marketing', title: 'Resizing images for email', desc: 'Couldn’t reach the simulated image service. No changes were made.', status: 'failed', ts: ago(42), target: { type: 'product', id: 'p-roll', label: 'Travel Watch Roll' }, next: 'Retry', details: ['3 images queued', 'Safe to retry'] },
      { id: 'a0', agent: 'support', title: 'Answered a shipping question', desc: 'Replied to Marco R. about delivery to Lisbon.', status: 'completed', ts: ago(55), target: { type: 'conversation', id: 'c2', label: 'Marco Ruiz' }, details: ['Used the store’s shipping policy', 'Sent in demo'] },
    ];
    const conversations = [
      { id: 'c1', customer: 'Hannah Whitfield', initials: 'HW', subject: 'Water resistance — Seamaster De Ville', channel: 'Store chat · simulated', productId: 'p-omega', agent: 'support', state: 'waiting', ts: ago(11),
        answer: '', rating: '', reply: '', replyEdited: false,
        handoff: 'It answered from the listing but couldn’t verify whether this watch has been pressure-tested.',
        messages: [
          { from: 'customer', text: 'Hi! I love the Seamaster De Ville in your new photos. Is it water resistant? I’d like to wear it swimming now and then.', ts: ago(13) },
          { from: 'system', text: 'Triage agent routed this to Support agent — product question', ts: ago(12.6) },
          { from: 'system', text: 'Support agent checked the listing, photos and store policies', ts: ago(12.2) },
          { from: 'agent', agent: 'support', text: 'Hi Hannah — thanks for your message! The case back is marked “Waterproof”, but on a vintage watch that describes the original specification: it depends on the seals and a recent pressure test. I’m checking with the shop whether it has been tested and will get back to you shortly.', ts: ago(11.8), note: 'Sent by the agent · demo' },
          { from: 'system', text: 'Support agent couldn’t verify a tested rating and handed the rest to you', ts: ago(11) },
        ] },
      { id: 'c3', customer: 'Priya Nair', initials: 'PN', subject: 'Chestnut strap in 20 mm?', channel: 'Email · simulated', productId: 'p-strap', agent: 'sales', state: 'agent', ts: ago(32),
        messages: [
          { from: 'customer', text: 'Does the chestnut calf strap come in 20 mm? My watch has wider lugs.', ts: ago(34) },
          { from: 'system', text: 'Triage agent routed this to Sales agent — variant question', ts: ago(33) },
          { from: 'agent', agent: 'sales', text: 'Hi Priya — the chestnut strap is currently listed in 18 mm only. I’ve noted your interest in 20 mm and will let you know if the store adds it.', ts: ago(32), note: 'Sent in demo' },
        ] },
      { id: 'c2', customer: 'Marco Ruiz', initials: 'MR', subject: 'Shipping to Lisbon', channel: 'Store chat · simulated', productId: 'p-stand', agent: 'support', state: 'resolved', ts: ago(55),
        messages: [
          { from: 'customer', text: 'How long does shipping to Lisbon take for the walnut stand?', ts: ago(57) },
          { from: 'agent', agent: 'support', text: 'Hi Marco — orders to Portugal usually arrive within 3–5 business days after dispatch. You’ll get a tracking link by email.', ts: ago(55), note: 'Sent in demo' },
          { from: 'customer', text: 'Perfect, thank you!', ts: ago(54) },
        ] },
      { id: 'c4', customer: 'Leon Becker', initials: 'LB', subject: 'Return for travel roll', channel: 'Email · simulated', productId: 'p-roll', agent: 'support', state: 'resolved', ts: ago(60 * 20),
        messages: [
          { from: 'customer', text: 'I ordered the travel roll as a gift but it’s the wrong colour. Can I return it?', ts: ago(60 * 21) },
          { from: 'agent', agent: 'support', text: 'Of course, Leon. Unused items can be returned within 30 days — I’ve sent a return label to your email.', ts: ago(60 * 20), note: 'Sent in demo' },
        ] },
    ];
    const campaign = {
      id: 'cmp-1', productId: 'p-omega', channel: 'Instagram post', imageId: 'w-dial', agentImageId: 'w-dial', status: 'pending',
      headline: 'Seamaster De Ville, on brown leather',
      body: 'A silver textured dial, black Arabic numerals and the Seamaster De Ville signature — on a hand-made top-grain leather strap. Full details and condition notes are in the listing.',
    };
    return { v: VERSION, products, decisions, activity, conversations, campaign };
  }

  // Publication requirements — proportionate, never blocked by optional specs.
  function requirements(p) {
    const stockOk = p.stock !== '' && p.stock != null && Number.isInteger(Number(p.stock)) && Number(p.stock) >= 0;
    const open = (p.claims || []).filter(c => c.state === 'open').length;
    return [
      { key: 'title', label: 'title', ok: !!(p.name || '').trim() },
      { key: 'description', label: 'description', ok: (p.description || '').trim().length >= 20 },
      { key: 'price', label: 'price', ok: Number(p.price) > 0 },
      { key: 'stock', label: 'quantity', ok: stockOk },
      { key: 'claims', label: open === 1 ? '1 claim' : open + ' claims', ok: open === 0 },
    ];
  }

  function supportReply(answer, rating, name) {
    const hi = 'Hi ' + (name || 'there') + ' — following up on your question about water resistance. ';
    if (answer === 'tested') return hi + 'This Seamaster De Ville was pressure-tested ' + (rating ? 'to ' + rating : '[add the tested rating]') + '. As with any vintage watch, we’d still suggest avoiding swimming, showers and saunas to protect the seals. Happy to help with anything else.';
    if (answer === 'not_tested') return hi + 'The case back is marked “Waterproof”, which describes the original specification. This watch hasn’t been pressure-tested recently, so we can’t confirm water resistance and recommend keeping it away from water. Happy to help with anything else.';
    if (answer === 'not_resistant') return hi + 'This watch shouldn’t be worn in water — its seals can’t be relied on, so please keep it dry, including when washing hands. Happy to help with anything else.';
    return hi + '[Waiting for your answer: has this watch been pressure-tested?]';
  }

  const GEN_STEPS = ['Reading photos', 'Preparing product details', 'Researching market price', 'Writing listing copy', 'Checking what needs your review'];

  const agents = {
    // Simulated: a real backend would upload images, run the Product and Research agents and stream task status.
    // Photos are the only input: the price comes from market research and quantity starts at 1.
    async generateListing(input, onStep) {
      for (let i = 0; i < GEN_STEPS.length; i++) { onStep && onStep(i); await wait(850); }
      const n = input.images.length, photos = n + ' photo' + (n === 1 ? '' : 's');
      let p;
      if (input.sample) {
        p = watchDraft({ images: input.images, coverId: input.coverId });
      } else {
        const research = GENERIC_RESEARCH();
        p = simpleProduct({
          id: uid('p'), name: 'New product', nameSrc: 'ai', nameNote: 'Placeholder — add a title',
          category: 'Other', categoryKey: 'other', status: 'needs_review', icon: 'package', images: input.images, coverId: input.coverId, descSrc: 'ai',
          price: research.suggested, priceSrc: 'research', research, stock: 1, stockSrc: 'default', condition: '',
          description: 'Shown in ' + photos + '. Add material, size and condition details so buyers know exactly what they’re getting.',
          observations: [{ id: 'o1', area: 'Photos', text: photos + ' grouped as one product', img: input.coverId }],
          updated: Date.now(), created: Date.now(),
        });
      }
      p.history = [
        { ts: Date.now(), who: 'You', text: 'Uploaded ' + photos },
        { ts: Date.now(), who: 'Product agent', text: 'Prepared listing draft' },
        { ts: Date.now(), who: 'Research agent', text: 'Suggested ' + euro(p.research.suggested) + ' from ' + p.research.comps + ' comparable listings (synthetic)' },
      ];
      return p;
    },
    async reviseListing(p, feedback) {
      await wait(2600);
      const first = (p.description || '').split(/(?<=\.)\s+/).slice(0, 2).join(' ');
      const kept = (p.claims || []).filter(c => c.state !== 'removed').map(c => c.text).filter(t => !first.includes(t));
      return { description: [first].concat(kept).join(' ').trim(), note: 'Revised per your feedback: “' + feedback.trim().slice(0, 80) + '”' };
    },
    async retryTask() { await wait(1800); return { ok: true }; },
  };

  window.CCDemo = { VERSION, AGENTS, GEN_STEPS, seed, requirements, supportReply, watchGallery: WATCH_GALLERY, agents, uid, euro };
})();
