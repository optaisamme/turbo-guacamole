// Headless render of a public job page (used only when server HTML is a JS shell).
// Usage: node render.mjs <url>   -> prints JSON to stdout. Page content is untrusted data.
// Does not log in, solve CAPTCHAs, or submit forms.
import { createRequire } from 'module';
import { promises as dns } from 'dns';
import net from 'net';
const require = createRequire(import.meta.url);

// Job pages are untrusted: the browser may load only normal public web addresses. Requests to this
// computer, a private network or non-web addresses are refused (the same rule as jobsearch/http.py).
export function privateAddress(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split('.').map(Number);
    return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
  }
  const v = ip.toLowerCase();
  if (v.startsWith('::ffff:')) return privateAddress(v.slice(7));
  return v === '::' || v === '::1' || v.startsWith('fc') || v.startsWith('fd') || v.startsWith('fe8') ||
    v.startsWith('fe9') || v.startsWith('fea') || v.startsWith('feb') || v.startsWith('ff');
}
export async function allowed(rawUrl) {
  let u;
  try { u = new URL(rawUrl); } catch (e) { return false; }
  if (u.protocol === 'data:' || u.protocol === 'blob:') return true;
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
  if (u.port && u.port !== '80' && u.port !== '443') return false;
  const host = u.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (host === 'localhost' || /\.(local|localhost|internal|lan|home\.arpa)$/.test(host)) return false;
  if (net.isIP(host)) return !privateAddress(host);
  try {
    const addrs = await dns.lookup(host, { all: true });
    return !addrs.some(a => privateAddress(a.address));
  } catch (e) { return true; }   // the name does not resolve here; the request itself will fail
}
if (process.argv[2] === '--self-test') {
  const cases = [['http://127.0.0.1/', false], ['http://169.254.169.254/latest/', false], ['http://10.1.2.3/', false],
    ['http://[::1]/', false], ['file:///etc/passwd', false], ['http://localhost:8080/', false],
    ['https://93.184.216.34/', true], ['data:text/plain,hi', true]];
  const results = [];
  for (const [u, want] of cases) results.push([u, (await allowed(u)) === want]);
  console.log(JSON.stringify(results)); process.exit(0);
}
let chromium;
try { ({ chromium } = require('playwright')); } catch (e) {
  console.log(JSON.stringify({ error: 'playwright not available' })); process.exit(0);
}
const url = process.argv[2];
const browser = await chromium.launch(process.env.PW_EXECUTABLE ? { executablePath: process.env.PW_EXECUTABLE } : {});
try {
  const page = await browser.newPage({ userAgent: process.env.JOBSEARCH_UA || undefined });
  await page.route('**/*', async route => (await allowed(route.request().url())) ? route.continue() : route.abort());
  const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  try { await page.waitForLoadState('networkidle', { timeout: 15000 }); } catch (e) {}
  const out = await page.evaluate(() => {
    const text = (document.body && document.body.innerText || '').slice(0, 60000);
    const forms = Array.from(document.querySelectorAll('form'));
    const hasFileInput = !!document.querySelector('input[type=file]');
    const applyLinks = Array.from(document.querySelectorAll('a,button'))
      .filter(el => /apply/i.test(el.innerText || el.getAttribute('aria-label') || ''))
      .slice(0, 10).map(el => ({ text: (el.innerText || '').trim().slice(0, 60), href: el.href || null }));
    return { title: document.title, text, form_count: forms.length, has_file_input: hasFileInput, apply_links: applyLinks };
  });
  out.final_url = page.url();
  out.status = resp ? resp.status() : null;
  console.log(JSON.stringify(out));
} catch (e) {
  console.log(JSON.stringify({ error: String(e).slice(0, 300) }));
} finally {
  await browser.close();
}
