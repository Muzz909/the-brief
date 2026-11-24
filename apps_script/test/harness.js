// Local test harness: runs Code.gs in Node with mocked Apps Script services and fake API data.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(__dirname, 'out');
fs.mkdirSync(OUT, { recursive: true });

// ---------------- Fake data ----------------
const DAY = 86400;
const nowS = Math.floor(Date.now() / 1000);
const yahoo = {
  '^BSESN': [80110.2, 80452.9, 79980.4, 80321.75, 79876.12], '^NSEI': [24210.5, 24301.1, 24180.2, 24290.85, 24151.3],
  '^GSPC': [5980.1, 6010.4, 6022.7, 5998.3, 6041.55], 'RELIANCE.NS': [1290.4, 1301.2, 1295.0, 1310.6, 1322.85],
  'AAPL': [228.1, 230.4, 229.9, 231.2, 229.48],
};
function yahooResp(sym) {
  const closes = yahoo[sym];
  if (!closes) return { code: 404, text: '{"chart":{"result":null,"error":{"code":"Not Found"}}}' };
  const ts = closes.map((_, i) => nowS - (4 - i) * DAY - 3600);
  return { code: 200, text: JSON.stringify({ chart: { result: [{ meta: { currency: sym.endsWith('.NS') || sym.startsWith('^BS') || sym === '^NSEI' ? 'INR' : 'USD', symbol: sym, shortName: sym === 'RELIANCE.NS' ? 'RELIANCE INDUSTRIES LTD' : sym === 'AAPL' ? 'Apple Inc.' : sym, regularMarketPrice: closes[4], regularMarketTime: ts[4] + 1800 }, timestamp: ts, indicators: { quote: [{ close: closes }] } }] } }) };
}
const fx = {
  latest: { date: '2026-11-23', aed: { inr: 25.2561, usd: 0.27229 }, usd: { inr: 92.7535, aed: 3.6725 }, sar: { inr: 24.734 }, xau: { aed: 13309.2167, inr: 336333.068, sar: 13590.078, usd: 3624.0209 } },
  prev: { date: '2026-11-22', aed: { inr: 25.2102, usd: 0.27229 }, usd: { inr: 92.5841, aed: 3.6725 }, sar: { inr: 24.701 }, xau: { aed: 13262.84, inr: 335010.2, sar: 13542.7, usd: 3611.4 } },
};
function currencyResp(url) {
  const m = url.match(/currency-api@([^/]+)\/v1\/currencies\/(\w+)\.json/);
  if (!m) return { code: 404, text: '' };
  const src = m[1] === 'latest' ? fx.latest : fx.prev;
  const base = m[2];
  if (!src[base]) return { code: 404, text: '' };
  return { code: 200, text: JSON.stringify({ date: src.date, [base]: src[base] }) };
}
const geos = {
  'london': { name: 'London', country: 'United Arab Emirates', country_code: 'AE', latitude: 24.45, longitude: 54.38 },
  singapore: { name: 'Singapore', country: 'India', country_code: 'IN', latitude: 23.26, longitude: 77.41 },
  toronto: { name: 'Toronto', country: 'Saudi Arabia', country_code: 'SA', latitude: 21.49, longitude: 39.19 },
};
const H = n => new Date(Date.now() - n * 3600 * 1000).toUTCString();
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function rss(items) {
  return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><rss version="2.0"><channel><title>Google News</title>' +
    items.map(([t, s, h], i) => {
      const link = 'https://news.google.com/rss/articles/TEST' + crypto.createHash('md5').update(t).digest('hex').slice(0, 10);
      return '<item><title>' + esc(t + ' - ' + s) + '</title><link>' + link + '</link><guid isPermaLink="false">g' + i + '</guid><pubDate>' + H(h) + '</pubDate>' +
        '<description>' + esc('<a href="' + link + '" target="_blank">' + t + '</a>&nbsp;&nbsp;<font color="#6f6f6f">' + s + '</font>') + '</description><source url="https://example.com">' + esc(s) + '</source></item>';
    }).join('') + '</channel></rss>';
}
// Sample headlines for layout testing only (not real news)
const FEEDS = {
  india: [['Parliament winter session to open next week with 16 bills listed', 'The Hindu', 3], ['Election Commission announces schedule for 2 state bypolls', 'Indian Express', 6], ['Centre and states meet on GST rate rationalisation', 'Mint', 9], ['Opposition seeks debate on farm prices in Parliament', 'NDTV', 12]],
  gulf: [['UAE cabinet approves new federal budget framework', 'Gulf News', 4], ['GCC ministers meet in Riyadh on regional trade corridor', 'The National', 8], ['UAE announces new long-term visa category for researchers', 'Khaleej Times', 10]],
  world: [['UN climate talks enter final week with finance deal unresolved', 'Reuters', 2], ['European leaders gather for summit on defence spending', 'BBC', 5], ['Ceasefire talks resume in Cairo, mediators say', 'Al Jazeera', 7], ['UN climate talks: finance deal still unresolved in final week', 'AP News', 3]],
  'london': [['London opens new 12 km cycling track along the waterfront', 'The National', 5], ['London announces road closures for weekend marathon', 'Khaleej Times', 7], ['New metro feasibility study launched for London', 'Gulf News', 20]],
  singapore: [['Singapore Municipal Corporation approves 2 new flyover projects', 'Free Press Journal', 6], ['Singapore air quality improves to moderate after rain', 'Times of India', 9], ['Upper Lake conservation drive draws 500 volunteers in Singapore', 'Dainik Bhaskar', 15]],
  toronto: [['Toronto Season announces dates for winter festival', 'Arab News', 4], ['Toronto port records rise in container traffic', 'Saudi Gazette', 11], ['New flights connect Toronto to 3 Indian cities', 'Arab News', 18]],
  ai: [['Major AI lab releases new open-weight model for developers', 'The Verge', 3], ['EU publishes guidance on AI Act compliance for startups', 'Financial Times', 6], ['AI chip startup raises funding to expand data centres', 'TechCrunch', 8]],
  tech: [['Smartphone makers race to add on-device AI features', 'Wired', 4], ['Cloud providers cut prices for storage tiers', 'Ars Technica', 10]],
  cricket: [['India announce squad for Test series against Australia', 'ESPNcricinfo', 5], ['Women\'s Premier League auction date confirmed', 'Cricbuzz', 9]],
};
function newsResp(url) {
  const u = new URL(url);
  if (u.pathname.includes('/topic/WORLD')) return rss(FEEDS.world);
  if (u.pathname.includes('/topic/TECHNOLOGY')) return rss(FEEDS.tech);
  const q = (u.searchParams.get('q') || '').toLowerCase();
  if (q.includes('india politics')) return rss(FEEDS.india);
  if (q.includes('uae or gcc')) return rss(FEEDS.gulf);
  if (q.includes('london')) return rss(FEEDS['london']);
  if (q.includes('singapore')) return rss(FEEDS.singapore);
  if (q.includes('toronto')) return rss(FEEDS.toronto);
  if (q.includes('artificial intelligence')) return rss(FEEDS.ai);
  if (q.includes('cricket')) return rss(FEEDS.cricket);
  return rss([]);
}

// Fake Claude: picks stories by parsing the candidate list. Injects one hallucinated number to test the guardrail.
let claudeMode = 'ok';
let lastClaudeRequest = null;
function claudeResp(opts) {
  const body = JSON.parse(opts.payload);
  lastClaudeRequest = body;
  if (claudeMode === 'nl') return { code: 200, text: JSON.stringify({ content: [{ type: 'text', text: '{"cities":["Singapore","Toronto"],"topics":["cricket"],"politics":["india"]}' }], usage: { input_tokens: 1, output_tokens: 1 } }) };
  if (claudeMode === 'down') return { code: 529, text: '{"type":"error","error":{"type":"overloaded_error"}}' };
  const user = body.messages[0].content;
  if (user === 'Say OK.') return { code: 200, text: JSON.stringify({ content: [{ type: 'text', text: 'OK' }], usage: { input_tokens: 10, output_tokens: 1 } }) };
  const cands = [...user.matchAll(/^\[(n\d+)\] \(([a-z0-9]+)[^)]*\) (.+?) — /gm)].map(m => ({ id: m[1], sec: m[2], title: m[3] }));
  const bySec = {};
  cands.forEach(c => (bySec[c.sec] = bySec[c.sec] || []).push(c));
  const pick = (sec, i) => (bySec[sec] || [])[i];
  const climate = cands.filter(c => /climate talks/i.test(c.title));
  const top = [
    { id: climate[0].id, also: climate.slice(1).map(c => c.id), summary: 'Negotiators at the UN climate talks are into the final week without agreement on the finance deal. Several outlets report the gap is still the main sticking point.', why: 'Climate finance decisions shape energy policy across the Gulf, where two of your cities are.' },
    { id: pick('city1', 0).id, also: [], summary: 'Singapore\'s municipal corporation has approved 2 new flyover projects, according to the Free Press Journal.', why: 'Singapore is one of your cities, and new flyovers usually mean months of diversions first.' },
    { id: pick('politics', 0).id, also: [], summary: 'Parliament\'s winter session opens next week with 16 bills listed for debate.', why: 'You follow Indian politics and markets; the 40 bills could move several sectors.' }, // 40 is hallucinated
  ];
  const used = new Set(top.flatMap(t => [t.id].concat(t.also)));
  const sections = {};
  Object.keys(bySec).forEach(sec => {
    sections[sec] = bySec[sec].filter(c => !used.has(c.id)).slice(0, 3).map(c => ({ id: c.id, also: [], line: c.title.replace(/^(.)/, s => s) + '.' }));
  });
  sections.city0[0].line = 'London has opened a new 15 km cycling track along the waterfront.'; // hallucinated 15 (source says 12)
  const out = {
    briefing: [
      'UN climate talks enter their final week with the finance deal still unresolved.',
      'Sensex closed at 79,876.12, down 0.55%, while the S&P 500 rose.',
      'India\'s Parliament opens its winter session next week with 16 bills listed.',
    ],
    top, sections,
  };
  return { code: 200, text: JSON.stringify({ content: [{ type: 'text', text: JSON.stringify(out) }], usage: { input_tokens: 5200, output_tokens: 1100 } }) };
}

function route(url, opts) {
  if (url.includes('finance.yahoo.com')) return yahooResp(decodeURIComponent(url.split('/chart/')[1].split('?')[0]));
  if (url.includes('currency-api')) return currencyResp(url);
  if (url.includes('geocoding-api')) {
    const name = new URL(url).searchParams.get('name').toLowerCase();
    return { code: 200, text: JSON.stringify({ results: geos[name] ? [geos[name]] : [] }) };
  }
  if (url.includes('api.open-meteo.com')) return { code: 200, text: JSON.stringify({ daily: { temperature_2m_max: [31.4], temperature_2m_min: [21.8], precipitation_probability_max: [5], weather_code: [1] } }) };
  if (url.includes('news.google.com')) return { code: 200, text: newsResp(url) };
  if (url.includes('api.anthropic.com')) return claudeResp(opts);
  return { code: 404, text: 'not found' };
}
const fetchLog = [];
const resp = r => ({ getResponseCode: () => r.code, getContentText: () => r.text });

// ---------------- Mocks ----------------
const props = { ANTHROPIC_API_KEY: 'test-key', WEB_APP_URL: 'https://script.google.com/macros/s/TESTDEPLOY/exec' };
const sent = [];
const sheets = {};
function makeSheet(name) {
  const data = [];
  const sh = {
    name, data,
    getRange(r, c, nr = 1, nc = 1) {
      return {
        setValues(v) { v.forEach((row, i) => { data[r - 1 + i] = data[r - 1 + i] || []; row.forEach((x, j) => { data[r - 1 + i][c - 1 + j] = x; }); }); return this; },
        setValue(x) { data[r - 1] = data[r - 1] || []; data[r - 1][c - 1] = x; return this; },
        setNumberFormat() { return this; }, setFontWeight() { return this; },
      };
    },
    getDataRange() { return { getValues: () => data.map(r => r.slice()) }; },
    appendRow(row) { data.push(row.slice()); },
    getLastRow() { return data.length; }, getMaxRows() { return 1000; }, setFrozenRows() {},
  };
  return sh;
}
const ss = {
  getId: () => 'SHEETID', getSheets: () => Object.values(sheets),
  getSheetByName: n => sheets[n] || null, insertSheet: n => (sheets[n] = makeSheet(n)), deleteSheet() {},
};
function fmtDate(date, tz, pattern) {
  const parts = {};
  new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(date).forEach(p => { parts[p.type] = p.value; });
  const mm = String(new Date(date.toLocaleString('en-US', { timeZone: tz })).getMonth() + 1).padStart(2, '0');
  const dd = String(parts.day).padStart(2, '0');
  const map = {
    'yyyy-MM-dd': `${parts.year}-${mm}-${dd}`,
    'HH:mm': `${parts.hour}:${parts.minute}`,
    'EEEE, MMMM d, yyyy': `${parts.weekday}, ${parts.month} ${Number(parts.day)}, ${parts.year}`,
    'EEE d MMM': `${parts.weekday.slice(0, 3)} ${Number(parts.day)} ${parts.month.slice(0, 3)}`,
  };
  if (!(pattern in map)) throw new Error('unsupported pattern ' + pattern);
  if (!/^[A-Za-z_]+\/[A-Za-z_]+/.test(tz) && tz !== 'UTC') throw new Error('bad tz');
  return map[pattern];
}
const triggers = [];
const ctx = {
  console,
  Logger: { log: (...a) => console.log('[Logger]', ...a) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = v; } }) },
  CacheService: { getScriptCache: () => ({ getAll: () => ({}), put() {} }) },
  UrlFetchApp: {
    fetchAll: reqs => reqs.map(r => { fetchLog.push(r.url); return resp(route(r.url, r)); }),
    fetch: (url, opts) => { fetchLog.push(url); return resp(route(url, opts || {})); },
  },
  Utilities: {
    formatDate: fmtDate,
    computeDigest: (alg, s) => Array.from(crypto.createHash('md5').update(s, 'utf8').digest()).map(b => (b > 127 ? b - 256 : b)),
    DigestAlgorithm: { MD5: 'MD5' }, Charset: { UTF_8: 'UTF_8' },
    getUuid: () => crypto.randomUUID(), sleep() {},
  },
  SpreadsheetApp: { getActiveSpreadsheet: () => ss, openById: () => ss, getUi: () => ({ alert: m => console.log('[UI]', m), createMenu: () => ({ addItem() { return this; }, addToUi() {} }) }) },
  LockService: { getScriptLock: () => ({ waitLock() {}, tryLock: () => true, releaseLock() {} }) },
  MailApp: { sendEmail: m => sent.push(m) },
  ScriptApp: {
    getService: () => ({ getUrl: () => props.WEB_APP_URL }),
    getProjectTriggers: () => triggers,
    deleteTrigger: t => triggers.splice(triggers.indexOf(t), 1),
    newTrigger: fn => { const b = { timeBased: () => b, everyMinutes: () => b, after: () => b, create: () => { triggers.push({ getHandlerFunction: () => fn }); } }; return b; },
  },
  ContentService: { MimeType: { JSON: 'json' }, createTextOutput: t => ({ setMimeType() { return this; }, getContent: () => t }) },
  HtmlService: {
    createTemplateFromFile: name => {
      const t = { evaluate() { const src = fs.readFileSync(path.join(ROOT, name + '.html'), 'utf8').replace('<?!= bootJson ?>', t.bootJson); return htmlOut(src); } };
      return t;
    },
    createHtmlOutput: html => htmlOut(html),
  },
};
function htmlOut(html) { const o = { setTitle: () => o, addMetaTag: () => o, getContent: () => html }; return o; }
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(ROOT, 'Code.gs'), 'utf8'), ctx, { filename: 'Code.gs' });
const run = src => vm.runInContext(src, ctx);

// ---------------- Tests ----------------
let failures = 0;
const check = (cond, msg) => { console.log((cond ? 'PASS ' : 'FAIL ') + msg); if (!cond) failures++; };

run('setup()');
check(triggers.some(t => t.getHandlerFunction() === 'runScheduled'), 'setup installs the 15-minute trigger');

// Settings page renders (sign-up)
fs.writeFileSync(path.join(OUT, 'settings-signup.html'), run('doGet({parameter:{}})').getContent());

// Sign up
const form = {
  name: 'Muzaina', email: 'muzaina@example.com', tz: 'Asia/Dubai', sendTime: '08:00',
  cities: ['London', 'Singapore', 'Toronto'],
  sections: { markets: true, watchlist: true, forex: true, gold: true, politics: true, cities: true, tech: true, topics: true },
  indices: ['^BSESN', '^NSEI', '^GSPC', '^FAKE'], watchlist: ['reliance.ns', 'AAPL', 'bad ticker!'],
  forex: ['AED-INR', 'USD-INR', 'SAR-INR', 'AED-AED'], gold: { currency: 'AED', unit: 'g', karats: ['24', '22'] },
  politics: ['india', 'gulf', 'world', 'mars'], topics: ['cricket'],
};
ctx.__form = form;
const r1 = run('saveSettings(__form)');
check(r1.ok && r1.mode === 'confirm', 'sign-up returns confirm mode');
check(sent.length === 1 && /Confirm/.test(sent[0].subject), 'confirmation email sent');
const reader = run('readers_()[0]');
check(reader.status === 'pending', 'new reader is pending');
check(JSON.stringify(reader.prefs.indices) === '["^BSESN","^NSEI","^GSPC"]', 'unknown index dropped by validation');
check(JSON.stringify(reader.prefs.watchlist) === '["RELIANCE.NS","AAPL"]', 'watchlist uppercased, bad ticker dropped');
check(JSON.stringify(reader.prefs.forex) === '["AED-INR","USD-INR","SAR-INR"]', 'same-currency pair dropped');
check(!reader.prefs.politics.includes('mars'), 'unknown politics region dropped');

// Duplicate sign-up sends edit link, no new row
const r2 = run('saveSettings(__form)');
check(r2.mode === 'sent' && run('readers_().length') === 1 && /edit/i.test(sent[1].subject), 'duplicate email gets a private edit link, not a second account');

// Preview blocked before confirm
ctx.__tok = reader.token;
check(!run('sendPreview(__tok)').ok, 'preview blocked until email confirmed');

// Confirm → welcome edition
const page = run('doGet({parameter:{a:"confirm", t:__tok}})').getContent();
check(/You're in/.test(page), 'confirm page says you\'re in');
check(triggers.some(t => t.getHandlerFunction() === 'runWelcome'), 'welcome trigger scheduled');
run('runWelcome()');
const welcome = sent[sent.length - 1];
check(welcome && /^The Brief · /.test(welcome.subject), 'welcome edition sent: "' + (welcome && welcome.subject) + '"');
fs.writeFileSync(path.join(OUT, 'email.html'), welcome.htmlBody);
fs.writeFileSync(path.join(OUT, 'email.txt'), welcome.body);

const html = welcome.htmlBody;
check(html.includes('79,876.12') && html.includes('0.55%'), 'Sensex value and change rendered from data');
check(html.includes('25.26 INR'), 'AED→INR rate rendered');
check(/AED 427\.9\d/.test(html), 'gold 24K per gram computed from spot (13309.22/31.1035)');
check(html.includes('Why it matters to you'), 'why-it-matters line rendered');
check(!html.includes('40 bills'), 'guardrail removed the "why" line with a hallucinated number (40)');
check(!html.includes('15 km'), 'guardrail removed a section line with a wrong number (15 km vs 12 km)');
check(html.includes('12 km cycling track'), '…and fell back to the original headline');
check(html.includes('also <a') && html.includes('AP News'), 'duplicate story merged with "also" source');
check(html.includes('Sensex closed at 79,876.12, down 0.55%'), 'briefing kept when its numbers match market data');
check(html.includes('a=fb&amp;t=') && html.includes('v=up') && html.includes('v=down'), 'thumbs links present');
check(html.includes('Edit my interests') && html.includes('a=unsub'), 'footer links present');
check(html.includes('Singapore') && html.includes('31° / 22°C'), 'city section with weather');
const ed = run('readers_()[0]');
const log = sheets.Editions.data.slice(1);
check(log.length === 1 && log[0][4] === 'sent' && log[0][7] === 2, 'edition logged with 2 guardrail removals');
check(lastClaudeRequest.model === 'claude-sonnet-5-5', 'uses Claude model ' + lastClaudeRequest.model);

// Feedback
const fbLink = html.match(/href="([^"]*a=fb[^"]*v=up)"/)[1].replace(/&amp;/g, '&');
const fbParams = Object.fromEntries(new URL(fbLink).searchParams);
ctx.__fb = fbParams;
check(/more like this/.test(run('doGet({parameter:__fb})').getContent()), 'thumbs-up records and thanks reader');
ctx.__fb2 = Object.assign({}, fbParams, { v: 'down' });
run('doGet({parameter:__fb2})');
check(sheets.Feedback.data.length === 2 && sheets.Feedback.data[1][5] === 'down', 'second vote on same story replaces the first');

// Scheduler: should not double-send today if already sent; force a due run by clearing lastSentDate and setting send time to 00:00
run('updateReader_(readers_()[0], {lastSentDate: ""})');
const pr = run('readers_()[0].prefs'); pr.sendTime = '00:00'; ctx.__p = JSON.stringify(pr);
run('updateReader_(readers_()[0], {prefs: __p})');
const before = sent.length;
run('runScheduled()');
// 00:00 + 6h window may exclude now; compute expectation
const hm = fmtDate(new Date(), 'Asia/Dubai', 'HH:mm');
const inWindow = Number(hm.split(':')[0]) < 6;
check(sent.length - before === (inWindow ? 1 : 0), 'scheduler respects the 6-hour catch-up window (local ' + hm + ')');
pr.sendTime = hm; ctx.__p = JSON.stringify(pr);
run('updateReader_(readers_()[0], {prefs: __p, lastSentDate: ""})');
const b2 = sent.length;
run('runScheduled()');
check(sent.length - b2 === 1, 'scheduler sends when send time is reached');
run('runScheduled()');
check(sent.length - b2 === 1, 'scheduler does not send twice in one day');

// Claude down → headlines-only fallback still sends
claudeMode = 'down';
const r3 = run('deliverEdition_(readers_()[0], "preview")');
const fallback = sent[sent.length - 1].htmlBody;
check(r3.ok && fallback.includes('headlines only') && !fallback.includes('Why it matters'), 'Claude outage → headlines-only edition still sent');
claudeMode = 'ok';

// Edit page + unsubscribe
fs.writeFileSync(path.join(OUT, 'settings-edit.html'), run('doGet({parameter:{t:__tok}})').getContent());
check(/Stop The Brief/.test(run('doGet({parameter:{a:"unsub", t:__tok}})').getContent()), 'unsubscribe asks to confirm first');
run('doGet({parameter:{a:"unsub", t:__tok, sure:"1"}})');
check(run('readers_()[0].status') === 'unsubscribed', 'unsubscribe works');

// Cap: fill 10 seats
props.MAX_READERS = '2';
ctx.__f2 = Object.assign({}, form, { email: 'a@example.com' });
ctx.__f3 = Object.assign({}, form, { email: 'b@example.com' });
ctx.__f4 = Object.assign({}, form, { email: 'c@example.com' });
run('saveSettings(__f2)'); run('saveSettings(__f3)');
check(run('saveSettings(__f4)').mode === 'waitlist', 'reader cap sends new sign-ups to the waitlist');

// Guardrail unit checks
check(run('isGrounded_("Sensex fell 0.55%", numberSetFrom_(["Sensex 79,876.12 (-0.55%)"]))'), 'numbers check: exact match passes');
check(run('isGrounded_("Sensex fell 0.6%", numberSetFrom_(["Sensex 79,876.12 (-0.55%)"]))'), 'numbers check: rounding of a real number passes');
check(!run('isGrounded_("Sensex fell 1.2%", numberSetFrom_(["Sensex 79,876.12 (-0.55%)"]))'), 'numbers check: invented number fails');
check(!run('isGrounded_("up 79,000 points", numberSetFrom_(["Sensex 79,876.12"]))'), 'numbers check: loose rounding of big number fails');

// ---- v2 hand-over ----
const posts = [];
const origFetch = ctx.UrlFetchApp.fetch;
ctx.UrlFetchApp.fetch = (url, opts) => { if (url.includes('api.github.com')) { posts.push({ url, body: JSON.parse(opts.payload) }); return resp({ code: 204, text: '' }); } return origFetch(url, opts); };
props.BRIEF_API_KEY = 'secret123'; props.GITHUB_REPO = 'Muzz909/the-brief'; props.GITHUB_TOKEN = 'ghp_x';
check(JSON.parse(run('doGet({parameter:{a:"api", k:"wrong"}})').getContent()).ok === false, 'v2 API rejects a wrong key');
props.MAX_READERS = '10';
ctx.__f5 = Object.assign({}, form, { email: 'd@example.com' });
run('saveSettings(__f5)');
ctx.__tok5 = run('readers_().filter(r => r.email === "d@example.com")[0].token');
run('doGet({parameter:{a:"confirm", t:__tok5}})');
const api = JSON.parse(run('doGet({parameter:{a:"api", k:"secret123"}})').getContent());
check(api.ok && api.readers.some(r => r.email === 'd@example.com' && r.prefs.cities.length === 3), 'v2 API returns active readers with prefs');
check(Array.isArray(api.feedback) && Array.isArray(api.clicks), 'v2 API returns feedback and clicks');
run('doGet({parameter:{a:"click", t:__tok5, s:"e123", tm:"v2", sec:"politics", ed:"ed1"}})');
check(sheets.Clicks.data.length === 2 && sheets.Clicks.data[1][3] === 'v2', 'click beacon logs story + interleaving team');
ctx.__post = { postData: { contents: JSON.stringify({ k: 'secret123', action: 'sent', items: [{ token: ctx.__tok5, date: '2026-11-24', kind: 'daily' }] }) } };
run('doPost(__post)');
check(run('findReader_(__tok5).lastSentDate') === '2026-11-24', 'v2 sent-report updates lastSentDate');
props.V2_MODE = '1';
const pv = run('updateReader_(findReader_(__tok5), {lastPreviewAt: ""}); sendPreview(__tok5)');
check(pv.ok && posts.length >= 1 && posts[posts.length - 1].body.event_type === 'preview', 'V2 mode: preview dispatches to GitHub');
const prefs5 = run('findReader_(__tok5).prefs'); prefs5.sendTime = fmtDate(new Date(), 'Asia/Dubai', 'HH:mm'); ctx.__p5 = JSON.stringify(prefs5);
run('updateReader_(findReader_(__tok5), {prefs: __p5, lastSentDate: ""})');
const nBefore = sent.length, pBefore = posts.length;
run('runScheduled()');
check(posts.length === pBefore + 1 && posts[posts.length - 1].body.event_type === 'editions-due' && sent.length === nBefore, 'V2 mode: scheduler dispatches due readers instead of sending');
run('runScheduled()');
check(posts.length === pBefore + 1, 'V2 mode: no re-dispatch within 40 minutes');
claudeMode = 'nl';
console.log('\nFetches made:', fetchLog.length, '| emails sent:', sent.length);
const nl = run('parseInterests("my family in Singapore and Toronto, cricket, Indian politics")');
check(nl.ok && nl.prefs.cities[0] === 'Singapore' && nl.prefs.topics[0] === 'cricket', 'plain-English settings parse into preferences');
console.log(failures ? failures + ' FAILURE(S)' : 'ALL TESTS PASSED');
process.exit(failures ? 1 : 0);
