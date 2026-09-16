const API_BASE = '';
const resultsEl = document.getElementById('results');
const summaryCountEl = document.getElementById('summaryCount');
const statusPillEl = document.getElementById('statusPill');
const loadingEl = document.getElementById('loading');

// Modal Elements
const dossierModal = document.getElementById('dossierModal');
const modalCloseBtn = document.getElementById('modalCloseBtn');
const modalCloseFooterBtn = document.getElementById('modalCloseFooterBtn');
const copyAddressBtn = document.getElementById('copyAddressBtn');
const modalLoading = document.getElementById('modalLoading');
const modalError = document.getElementById('modalError');

const modalChainEl = document.getElementById('modalChain');
const modalSourceEl = document.getElementById('modalSource');
const modalSymbolEl = document.getElementById('modalSymbol');
const modalNameEl = document.getElementById('modalName');
const modalAddressEl = document.getElementById('modalAddress');

const dossierPriceEl = document.getElementById('dossierPrice');
const dossierMarketCapEl = document.getElementById('dossierMarketCap');
const dossierLiquidityEl = document.getElementById('dossierLiquidity');

const dossierDevHoldEl = document.getElementById('dossierDevHold');
const dossierTop10El = document.getElementById('dossierTop10');
const dossierBundlerEl = document.getElementById('dossierBundler');
const dossierFreshEl = document.getElementById('dossierFresh');
const dossierRugRatioEl = document.getElementById('dossierRugRatio');
const dossierMintRenouncedEl = document.getElementById('dossierMintRenounced');

const dossierSmartMoneyEl = document.getElementById('dossierSmartMoney');
const dossierKolCountEl = document.getElementById('dossierKolCount');
const dossierKlinesTableBody = document.getElementById('dossierKlinesTableBody');

let currentChain = 'sol';

function setStatus(text, tone = 'info') {
  statusPillEl.textContent = text;
  statusPillEl.style.color = tone === 'error' ? '#ffb3b3' : '#62d0ff';
  statusPillEl.style.borderColor = tone === 'error' ? 'rgba(255, 122, 122, 0.45)' : 'rgba(98, 208, 255, 0.35)';
  statusPillEl.style.background = tone === 'error' ? 'rgba(255, 122, 122, 0.08)' : 'rgba(98, 208, 255, 0.12)';
}

function formatNumber(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  if (num >= 1_000_000) return `$${(num / 1_000_000).toFixed(2)}M`;
  if (num >= 1_000) return `$${(num / 1_000).toFixed(2)}K`;
  if (num >= 1) return `$${num.toFixed(2)}`;
  if (num > 0) return `$${num.toFixed(6)}`;
  return '$0.00';
}

function formatPrice(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num === 0) return '—';
  if (num < 0.000001) return `$${num.toExponential(4)}`;
  if (num < 0.01) return `$${num.toFixed(7)}`;
  if (num < 1) return `$${num.toFixed(4)}`;
  return `$${num.toFixed(2)}`;
}

function formatValue(value, formatter = formatNumber) {
  const num = Number(value);
  if (value === null || value === undefined || value === '' || !Number.isFinite(num) || num === 0) {
    return 'No disponible';
  }
  return formatter(value);
}

function formatUSD(value) {
  return formatValue(value, formatNumber);
}

function formatPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '—';
  return `${(num * 100).toFixed(2)}%`;
}

function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(Number(ts));
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function normalizeToken(token = {}) {
  const price = token.price ?? token.price_usd ?? token.priceUsd ?? 0;
  const mc = token.market_cap_usd ?? token.marketCap ?? token.usd_market_cap ?? token.market_cap ?? 0;
  const liq = token.liquidity ?? token.liquidity_usd ?? token.liquidityUsd ?? 0;
  const top10 = token.top_10_holder_rate ?? token.top10HolderRate ?? token.top_holder_rate ?? token.top_10_holders ?? 0;
  const bundler = token.bundler_rate ?? token.bundlerRate ?? token.bundler_trader_amount_rate ?? 0;
  const fresh = token.fresh_wallet_rate ?? token.freshWalletRate ?? 0;
  const rug = token.rug_ratio ?? 0;
  const sm = token.smart_degen_count ?? token.smart_wallets ?? token.smart_money_count ?? 0;
  const kol = token.renowned_count ?? token.kol_count ?? 0;
  const devHold = token.dev_team_hold_rate ?? token.dev_hold ?? 0;
  const renMint = [true, 1, '1'].includes(token.renounced_mint) || token.renounced_mint === undefined;

  return {
    address: token.address || '—',
    symbol: token.symbol || token.name || 'UNKNOWN',
    name: token.name || token.symbol || 'Unknown',
    price: price,
    market_cap: mc,
    marketCap: mc,
    liquidity: liq,
    top_10_holders: top10,
    topHolder: top10,
    bundler_rate: bundler,
    bundler: bundler,
    fresh_wallet_rate: fresh,
    fresh: fresh,
    rug_ratio: rug,
    rugRatio: rug,
    smart_money_count: sm,
    smartWallets: sm,
    kol_count: kol,
    kolCount: kol,
    dev_hold: devHold,
    devHold: devHold,
    renounced_mint: renMint,
    chain: token.chain || currentChain || 'sol',
  };
}

function renderTokens(tokens) {
  resultsEl.innerHTML = '';

  if (!tokens || tokens.length === 0) {
    resultsEl.innerHTML = '<div class="empty-state">No tokens matched your filter settings.</div>';
    summaryCountEl.textContent = '0 tokens';
    return;
  }

  summaryCountEl.textContent = `${tokens.length} token${tokens.length === 1 ? '' : 's'}`;

  tokens.forEach((token) => {
    const normalized = normalizeToken(token);
    const card = document.createElement('article');
    card.className = 'token-card clickable-card';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.title = 'Haz clic para abrir el Dossier detallado';

    card.innerHTML = `
      <div class="token-header">
        <div>
          <div class="symbol-badge-row">
            <span class="symbol">${normalized.symbol}</span>
            <span class="badge-action">Ver Dossier ➔</span>
          </div>
          <div class="name">${normalized.name}</div>
          <div class="address">${normalized.address}</div>
        </div>
      </div>
      <div class="metric-list">
        <div><span class="metric-label">Price</span></div>
        <div class="metric-value text-accent">${formatPrice(normalized.price)}</div>

        <div><span class="metric-label">MCap</span></div>
        <div class="metric-value">${formatNumber(normalized.marketCap)}</div>

        <div><span class="metric-label">Liquidity</span></div>
        <div class="metric-value">${formatNumber(normalized.liquidity)}</div>

        <div><span class="metric-label">Top 10</span></div>
        <div class="metric-value">${formatPercent(normalized.topHolder)}</div>

        <div><span class="metric-label">Bundler</span></div>
        <div class="metric-value">${formatPercent(normalized.bundler)}</div>

        <div><span class="metric-label">Fresh</span></div>
        <div class="metric-value">${formatPercent(normalized.fresh)}</div>
      </div>
    `;

    card.addEventListener('click', () => openDossier(token.address, token.chain || 'sol'));
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openDossier(token.address, token.chain || 'sol');
      }
    });

    resultsEl.appendChild(card);
  });
}

function openModal() {
  dossierModal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  dossierModal.classList.add('hidden');
  document.body.style.overflow = '';
}

async function openDossier(address, chain = 'sol', prefill = null) {
  if (typeof address !== 'string' || !address.trim() || address === '—') return;

  const resolvedChain = chain || 'sol';
  modalAddressEl.textContent = address;
  modalChainEl.textContent = resolvedChain.toUpperCase();

  // Immediately prefill and display all known metrics with 0 lag
  if (prefill) {
    renderDossier(prefill);
  } else {
    modalSymbolEl.textContent = 'Cargando...';
    modalNameEl.textContent = '—';
    dossierPriceEl.textContent = '—';
    dossierMarketCapEl.textContent = '—';
    dossierLiquidityEl.textContent = '—';
    dossierTop10El.textContent = '—';
    dossierBundlerEl.textContent = '—';
    dossierFreshEl.textContent = '—';
    dossierDevHoldEl.textContent = '—';
    dossierRugRatioEl.textContent = '—';
    dossierMintRenouncedEl.textContent = '—';
    dossierSmartMoneyEl.textContent = '—';
    dossierKolCountEl.textContent = '—';
  }

  copyAddressBtn.textContent = 'Copiar';
  dossierKlinesTableBody.innerHTML = '<tr><td colspan="7" class="text-center">Consultando velas de 5m...</td></tr>';
  modalError.classList.add('hidden');
  modalError.textContent = '';
  modalLoading.classList.remove('hidden');

  openModal();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 15000);

  try {
    const url = `${API_BASE}/api/dossier?chain=${encodeURIComponent(resolvedChain)}&address=${encodeURIComponent(address)}`;
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timeoutId);

    if (!response.ok) {
      const errText = await response.text();
      let msg = errText;
      try {
        const jsonErr = JSON.parse(errText);
        msg = jsonErr.detail || errText;
      } catch (_) {}
      throw new Error(msg || `HTTP ${response.status}`);
    }

    const data = await response.json();
     renderDossier(data);
  } catch (err) {
    if (err.name === 'AbortError') {
      console.warn('La consulta de velas tardó más de 15s; mostrando datos base pre-cargados.');
    } else {
      modalError.textContent = `Aviso: ${err.message}`;
      modalError.classList.remove('hidden');
    }
  } finally {
    clearTimeout(timeoutId);
    modalLoading.classList.add('hidden');
  }
}

function renderDossier(data) {
  if (!data) return;

  modalSymbolEl.textContent = data.symbol || 'UNKNOWN';
  modalNameEl.textContent = data.name || data.symbol || '—';
  modalChainEl.textContent = (data.chain || currentChain || 'sol').toUpperCase();
  modalSourceEl.textContent = data.source || 'GMGN';
  modalAddressEl.textContent = data.address || '—';

  // Overview
  dossierPriceEl.textContent = formatValue(data.price, formatPrice);
  dossierMarketCapEl.textContent = formatUSD(data.market_cap ?? data.marketCap);
  dossierLiquidityEl.textContent = formatUSD(data.liquidity);
  if (!Number(data.price) && !Number(data.market_cap) && !Number(data.liquidity)) {
    modalError.textContent = 'No hay datos disponibles para este token.';
    modalError.classList.remove('hidden');
  }

  // Security
  const devHold = Number(data.dev_hold ?? data.devHold ?? 0);
  dossierDevHoldEl.textContent = formatPercent(devHold);
  dossierDevHoldEl.className = `metric-val ${devHold === 0 ? 'text-success' : (devHold > 0.05 ? 'text-danger' : 'text-warning')}`;

  const top10 = Number(data.top_10_holders ?? data.topHolder ?? 0);
  dossierTop10El.textContent = formatPercent(top10);
  dossierTop10El.className = `metric-val ${top10 <= 0.2 ? 'text-success' : 'text-warning'}`;

  const bundler = Number(data.bundler_rate ?? data.bundler ?? 0);
  dossierBundlerEl.textContent = formatPercent(bundler);
  dossierBundlerEl.className = `metric-val ${bundler <= 0.1 ? 'text-success' : 'text-warning'}`;

  const fresh = Number(data.fresh_wallet_rate ?? data.fresh ?? 0);
  dossierFreshEl.textContent = formatPercent(fresh);

  const rug = Number(data.rug_ratio ?? data.rugRatio ?? 0);
  dossierRugRatioEl.textContent = Number.isFinite(rug) ? rug.toFixed(2) : '—';
  dossierRugRatioEl.className = `metric-val ${rug === 0 ? 'text-success' : (rug > 0.3 ? 'text-danger' : 'text-warning')}`;

  const renMint = data.renounced_mint ?? data.renouncedMint;
  const isRenounced = renMint === true || renMint === 1 || renMint === '1';
  dossierMintRenouncedEl.textContent = isRenounced ? '✅ Sí' : '❌ No';
  dossierMintRenouncedEl.className = `metric-val ${isRenounced ? 'text-success' : 'text-danger'}`;

  // Smart Money
  const smCount = data.smart_money_count ?? data.smartWallets ?? 0;
  const kolCount = data.kol_count ?? data.kolCount ?? 0;
  dossierSmartMoneyEl.textContent = `${smCount} wallets`;
  dossierKolCountEl.textContent = `${kolCount} KOLs`;

  // Klines
  renderKlines(data.klines);
}

function renderKlines(klines) {
  if (!Array.isArray(klines) || klines.length === 0) {
    dossierKlinesTableBody.innerHTML = '<tr><td colspan="7" class="text-center">No hay datos de velas disponibles</td></tr>';
    return;
  }

  // Reverse so latest is on top
  const rows = [...klines].reverse().map((candle) => {
    const o = Number(candle.open || 0);
    const c = Number(candle.close || 0);
    const h = Number(candle.high || 0);
    const l = Number(candle.low || 0);
    const vol = Number(candle.volume || 0);
    const change = o > 0 ? ((c - o) / o) * 100 : 0;
    const isUp = change >= 0;
    const changeClass = isUp ? 'text-success' : 'text-danger';
    const sign = isUp ? '+' : '';

    return `
      <tr>
        <td>${formatTime(candle.time)}</td>
        <td class="${changeClass} font-bold">${formatPrice(c)}</td>
        <td>${formatPrice(o)}</td>
        <td>${formatPrice(h)}</td>
        <td>${formatPrice(l)}</td>
        <td>${formatNumber(vol)}</td>
        <td class="${changeClass}">${sign}${change.toFixed(2)}%</td>
      </tr>
    `;
  });

  dossierKlinesTableBody.innerHTML = rows.join('');
}

async function loadTokens(formData) {
  loadingEl.classList.remove('hidden');
  setStatus('Requesting data...');

  try {
    currentChain = formData.get('chain') || 'sol';
    const payload = {
      chain: currentChain,
      min_marketcap: Number(formData.get('min_marketcap')) || 50000,
      max_marketcap: Number(formData.get('max_marketcap')) || 200000,
      min_liquidity: Number(formData.get('min_liquidity')) || 10000,
      max_top_holder_rate: Number(formData.get('max_top_holder_rate')) || 0.2,
      max_bundler_rate: Number(formData.get('max_bundler_rate')) || 0.2,
      max_fresh_wallet_rate: Number(formData.get('max_fresh_wallet_rate')) || 0.2,
      limit: Number(formData.get('limit')) || 80,
    };

    const response = await fetch(`${API_BASE}/api/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }

    const data = await response.json();
    const tokens = Array.isArray(data.tokens) ? data.tokens : [];
    renderTokens(tokens);
    setStatus(`OK: ${tokens.length} tokens`);
  } catch (err) {
    resultsEl.innerHTML = `<div class="empty-state">Failed to load tokens: ${err.message}</div>`;
    setStatus('Backend unavailable', 'error');
    summaryCountEl.textContent = 'Request failed';
  } finally {
    loadingEl.classList.add('hidden');
  }
}

async function checkBackend() {
  try {
    const response = await fetch(`${API_BASE}/health`);
    if (!response.ok) throw new Error('Health check failed');
    setStatus('Backend online');
    return true;
  } catch (err) {
    setStatus('Backend offline', 'error');
    return false;
  }
}

// Event Listeners
document.getElementById('filtersForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  await loadTokens(form);
});

modalCloseBtn.addEventListener('click', closeModal);
modalCloseFooterBtn.addEventListener('click', closeModal);

dossierModal.addEventListener('click', (event) => {
  if (event.target === dossierModal) {
    closeModal();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !dossierModal.classList.contains('hidden')) {
    closeModal();
  }
});

copyAddressBtn.addEventListener('click', async () => {
  const addr = modalAddressEl.textContent.trim();
  if (addr && addr !== '—') {
    try {
      await navigator.clipboard.writeText(addr);
      copyAddressBtn.textContent = '¡Copiado!';
      setTimeout(() => {
        copyAddressBtn.textContent = 'Copiar';
      }, 2000);
    } catch (_) {
      copyAddressBtn.textContent = 'Error';
    }
  }
});

(async function init() {
  const ready = await checkBackend();
  if (ready) {
    const form = new FormData(document.getElementById('filtersForm'));
    await loadTokens(form);
  }
})();
