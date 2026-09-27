#!/usr/bin/env node
/**
 * Ghostery AdBlocker Node server.
 *
 * Exposes the @ghostery/adblocker FiltersEngine via a tiny HTTP API so the
 * Python side of BFSB can query it for network filtering, cosmetic
 * injection, and scriptlet injection.
 *
 * Protocol:
 *   POST /match        { url, sourceUrl, resourceType } -> { blocked, redirect, exception, filter }
 *   POST /cosmetics    { url, hostname, domain }        -> { styles, scripts, extended }
 *   POST /scriptlet    { url }                            -> { script }
 *   GET  /stats        -> { lists, ... }
 *
 * The server listens on a random localhost port and prints a JSON line
 * containing {port, pid, engineVersion} once the engine is loaded,
 * so the Python parent knows when to start sending requests.
 */

const http = require('http');
const { FiltersEngine, Request } = require('@ghostery/adblocker');

const GHOSTERY_PORT = parseInt(process.env.GHOSTERY_PORT || '0', 10);
const YOUTUBE_DOMAINS = ['youtube.com', 'youtu.be'];

function getScriptletForUrl(url) {
  // Ghostery does not ship a YouTube-specific scriptlet; we generate a
  // minimal one for actual YouTube domains. Privacy frontends (yewtu.be,
  // piped.*, inv.nadeko.net) are intentionally excluded.
  try {
    const host = (new URL(url)).hostname || '';
    const isYouTube = YOUTUBE_DOMAINS.some((d) => host === d || host.endsWith('.' + d));
    if (!isYouTube) return '';
    return [
      "(function(){",
      "  if(window.__bfsbYTScriptlet) return;",
      "  window.__bfsbYTScriptlet=true;",
      "  var adSel=['.ytp-ad-module','.ytp-ad-player-overlay','.ytp-ad-overlay-container','.ytp-ad-overlay-slot','.ytp-ad-overlay-image','.ytp-ad-slot','.ytp-ad-text-slot','.ytp-ad-companion-slot','.ytp-ad-skip-button','.ytp-ad-skip-button-modern','.ytp-ad-player-overlay-skip-button','.ytp-ad-overlay-close-button','.ytp-ad-player-overlay-close-button','.videoAdUi','.adShowing','.ad-container','.ad-slot','.ytp-ad-interstitial','.ytp-ad-notification-container','.ytp-ad-preview-container','.ytp-ad-image-overlay','.ytp-ad-simple-ad-badge','ytd-promoted-sparkles-web-renderer','ytd-ad-slot-renderer','ytd-companion-slot-renderer','ytd-display-ad-renderer','ytd-in-feed-ad-layout-renderer','ytd-banner-promo-renderer','ytd-statement-banner-renderer','ytd-action-companion-ad-renderer','ytd-video-masthead-ad-primary-renderer','ytd-video-masthead-ad-advertiser-info-renderer','ytd-video-masthead-ad-video-renderer','ytd-search-pyv-renderer','ytd-reel-shelf-renderer','.masthead-ad-control'];",
      "  function rm(){for(var i=adSel.length-1;i>=0;i--){var e=document.querySelectorAll(adSel[i]);for(var j=0;j<e.length;j++){try{e[j].remove();}catch(err){}}}}",
      "  var v=document.querySelector('video');if(v){var f=function(){if(v.currentTime<v.duration){v.currentTime=v.duration}};v.addEventListener('adstart',f);v.addEventListener('adplaying',f);}}",
      "})();",
    ].join('');
  } catch (e) {
    return '';
  }
}

let engine = null;

async function initEngine() {
  console.error('[Ghostery] Loading engine with Ads + Tracking filter lists...');
  try {
    engine = await FiltersEngine.fromPrebuiltAdsAndTracking(fetch);
  } catch (e) {
    console.error('[Ghostery] fromPrebuiltAdsAndTracking failed, falling back to parse empty:', e && e.message);
    engine = FiltersEngine.parse('');
  }
  const listCount = engine.lists ? engine.lists.size : 0;
  console.error('[Ghostery] Engine ready. Loaded lists:', listCount);
  const addr = server.address();
  process.stdout.write(JSON.stringify({ port: addr.port, pid: process.pid, engineVersion: engine.constructor && engine.constructor.name, lists: listCount }) + '\n');
}

function parseResourceType(rt) {
  // Map Qt resource types to Ghostery/Chromium types accepted by Ghostery.
  const map = {
    0: 'main_frame',
    1: 'sub_frame',
    2: 'stylesheet',
    3: 'script',
    4: 'image',
    5: 'font',
    6: 'other',
    7: 'object',
    8: 'media',
    13: 'xhr',
    14: 'ping',
    21: 'xhr',
    254: 'websocket',
    255: 'other',
  };
  if (rt === null || rt === undefined || rt === '') return 'other';
  if (typeof rt === 'string') return rt;
  return map[rt] || 'other';
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    let tooBig = false;
    req.on('data', (chunk) => {
      data += chunk;
      if (data.length > 1024 * 1024) { tooBig = true; req.destroy(); }
    });
    req.on('end', () => {
      if (tooBig) { reject(new Error('payload too large')); return; }
      if (!data) { resolve({}); return; }
      try { resolve(JSON.parse(data)); }
      catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

function sendJson(res, obj, status = 200) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(body);
}

function sendError(res, e) {
  sendJson(res, { error: e && e.message ? e.message : String(e) }, 400);
}

const server = http.createServer(async (req, res) => {
  try {
    if (engine === null) {
      sendJson(res, { error: 'engine not loaded' }, 503);
      return;
    }
    const parts = req.url.split('?')[0].split('/');
    const path = '/' + (parts[1] || '');

    if (path === '/match' && req.method === 'POST') {
      const body = await parseBody(req);
      const rt = parseResourceType(body.resourceType);
      const request = Request.fromRawDetails({
        url: body.url || '',
        type: rt,
        sourceUrl: body.sourceUrl || '',
      });
      const result = engine.match(request);
      sendJson(res, {
        match: Boolean(result.match),
        redirect: result.redirect,
        exception: result.exception ? String(result.exception) : undefined,
        filter: result.filter ? String(result.filter) : undefined,
      });
      return;
    }

    if (path === '/cosmetics' && req.method === 'POST') {
      const body = await parseBody(req);
      const url = body.url || '';
      let hostname = body.hostname || '';
      let domain = body.domain || '';
      if (!hostname || !domain) {
        try {
          const u = new URL(url);
          hostname = hostname || u.hostname || '';
          domain = domain || u.hostname || '';
        } catch (e) {}
      }
      const result = engine.getCosmeticsFilters({ url, hostname, domain });
      sendJson(res, {
        active: result.active,
        styles: result.styles || '',
        scripts: result.scripts || [],
        extended: result.extended || [],
      });
      return;
    }

    if (path === '/scriptlet' && req.method === 'POST') {
      const body = await parseBody(req);
      const url = body.url || '';
      let script = '';
      // Ghostery may provide scriptlets via cosmetics for some lists; supplement
      // with our YouTube scriptlet for actual YouTube domains.
      try {
        let hostname = '';
        try { hostname = (new URL(url)).hostname || ''; } catch (e) {}
        if (YOUTUBE_DOMAINS.some((d) => hostname === d || hostname.endsWith('.' + d))) {
          script += getScriptletForUrl(url);
        }
      } catch (e) {}
      sendJson(res, { script });
      return;
    }

    if (path === '/stats' && req.method === 'GET') {
      const n = engine.getFilters ? engine.getFilters() : null;
      sendJson(res, {
        lists: engine.lists ? Array.from(engine.lists.keys()) : [],
        networkFilters: n ? n.networkFilters.length : -1,
        cosmeticFilters: n ? n.cosmeticFilters.length : -1,
      });
      return;
    }

    sendJson(res, { error: 'not found' }, 404);
  } catch (e) {
    sendError(res, e);
  }
});

server.on('error', (e) => {
  if (e.code === 'EADDRINUSE') {
    console.error('[Ghostery] EADDRINUSE, retrying...');
    server.listen(0, '127.0.0.1', () => initEngine());
  } else {
    console.error('[Ghostery] server error:', e);
  }
});

server.listen(GHOSTERY_PORT, '127.0.0.1', () => {
  initEngine();
});
