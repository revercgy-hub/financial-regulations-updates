(() => {
  'use strict';

  const DATA = Array.isArray(window.KB_CATALOG) ? window.KB_CATALOG : [];
  const SHARDS = Array.isArray(window.KB_SEARCH_SHARDS) ? window.KB_SEARCH_SHARDS : [];
  const PAGE_SIZE = 60;
  const STATE_KEY = 'finreg.searchState.v1';
  const input = document.getElementById('searchInput');
  const collectionFilter = document.getElementById('collectionFilter');
  const categoryFilter = document.getElementById('categoryFilter');
  const yearFilter = document.getElementById('yearFilter');
  const results = document.getElementById('results');
  const resultMeta = document.getElementById('resultMeta');
  const loadMore = document.getElementById('loadMore');
  let visible = PAGE_SIZE;
  let matches = [];
  let debounceTimer = 0;
  let searchGeneration = 0;
  let activeRequest = null;
  let pendingRestoreScroll = null;

  const esc = value => String(value || '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
  const queryTerms = value => value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const yearOf = value => {
    const found = String(value || '').match(/(?:19|20)\d{2}/);
    return found ? found[0] : '';
  };
  const titleText = item => String(item.title || '').toLocaleLowerCase();
  const metaText = item => [
    item.collection, item.category, item.agency, item.file_no, item.date, item.status
  ].filter(Boolean).join(' ').toLocaleLowerCase();

  function readSearchState() {
    try {
      const value = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
      if (!value || typeof value !== 'object') return null;
      const active = value.q || value.collection || value.category || value.year;
      return active ? value : null;
    } catch (_) {
      return null;
    }
  }

  function clearSearchState() {
    try {
      localStorage.removeItem(STATE_KEY);
    } catch (_) {}
  }

  function saveSearchState() {
    if (!hasActiveSearch()) {
      clearSearchState();
      return;
    }
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        q: input.value,
        collection: collectionFilter.value,
        category: categoryFilter.value,
        year: yearFilter.value,
        visible,
        scrollY: Math.max(0, Math.round(window.scrollY || 0))
      }));
    } catch (_) {}
  }

  function populate(select, values, current, firstLabel) {
    const sorted = [...new Set(values.filter(Boolean))]
      .sort((a, b) => String(a).localeCompare(String(b), 'zh-CN'));
    select.innerHTML = `<option value="">${esc(firstLabel)}</option>` +
      sorted.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
    if (sorted.includes(current)) select.value = current;
  }

  function refreshCategoryOptions() {
    const current = categoryFilter.value;
    const collection = collectionFilter.value;
    populate(
      categoryFilter,
      DATA.filter(item => !collection || item.collection_id === collection).map(item => item.category),
      current,
      '全部分类'
    );
  }

  function hasActiveSearch() {
    return Boolean(
      input.value.trim() || collectionFilter.value || categoryFilter.value || yearFilter.value
    );
  }

  function stopCurrentSearch() {
    searchGeneration += 1;
    if (activeRequest) {
      activeRequest.abort();
      activeRequest = null;
    }
  }

  function setCollection(value) {
    collectionFilter.value = value;
    document.querySelectorAll('.collection-card').forEach(card => {
      card.setAttribute('aria-pressed', String(card.dataset.collection === value));
    });
    refreshCategoryOptions();
    visible = PAGE_SIZE;
    render();
  }

  function passesFilters(item, collection, category, year) {
    if (collection && item.collection_id !== collection) return false;
    if (category && item.category !== category) return false;
    if (year && yearOf(item.date) !== year) return false;
    return true;
  }

  function score(item, phrase, terms) {
    if (!terms.length) return Number(item.sort_date || 0);
    const title = titleText(item);
    const meta = metaText(item);
    let total = title.includes(phrase) ? 120 : 0;
    for (const term of terms) {
      if (title.includes(term)) total += 36;
      if (meta.includes(term)) total += 12;
    }
    return total;
  }

  function makeSnippet(text, terms) {
    let position = -1;
    for (const term of terms) {
      const found = text.indexOf(term);
      if (found >= 0 && (position < 0 || found < position)) position = found;
    }
    if (position < 0) return text.slice(0, 190);
    const start = Math.max(0, position - 58);
    return `${start ? '…' : ''}${text.slice(start, start + 230)}${start + 230 < text.length ? '…' : ''}`;
  }

  function highlight(value, terms) {
    let output = esc(value);
    for (const term of terms.slice(0, 5)) {
      if (!term) continue;
      const safe = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      output = output.replace(new RegExp(safe, 'gi'), match => `<mark>${match}</mark>`);
    }
    return output;
  }

  function renderResults(terms) {
    const shown = matches.slice(0, visible);
    if (!shown.length) {
      results.innerHTML = '<div class="empty"><strong>没有找到匹配文档</strong>请减少关键词，或清除分类和年份筛选后重试。</div>';
      loadMore.hidden = true;
      return;
    }
    results.innerHTML = shown.map(result => {
      const item = result.item;
      const meta = [item.agency, item.date, item.status, item.file_no]
        .filter(Boolean).map(value => `<span>${esc(value)}</span>`).join('');
      const excerpt = result.snippet
        ? `<p>${highlight(result.snippet, terms)}</p>`
        : '';
      return `<a class="result" href="${esc(item.page_path)}">
        <div class="result-kicker"><b>${esc(item.collection)}</b><span>${esc(item.category)}</span></div>
        <div class="result-title">${highlight(item.title, terms)}</div>
        <div class="result-meta">${meta}</div>${excerpt}
      </a>`;
    }).join('');
    loadMore.hidden = visible >= matches.length;
    loadMore.textContent = `显示更多（剩余 ${Math.max(0, matches.length - visible).toLocaleString()} 条）`;
  }

  function scopeText() {
    return [
      collectionFilter.options[collectionFilter.selectedIndex]?.text,
      categoryFilter.value,
      yearFilter.value
    ].filter(value => value && !value.startsWith('全部')).join(' · ');
  }

  function finishResults(terms, scope) {
    matches.sort((a, b) =>
      b.score - a.score ||
      String(b.item.sort_date).localeCompare(String(a.item.sort_date)) ||
      a.item.title.localeCompare(b.item.title, 'zh-CN')
    );
    resultMeta.textContent =
      `${scope ? scope + '｜' : ''}找到 ${matches.length.toLocaleString()} 篇${terms.length ? '匹配文档' : '文档'}`;
    renderResults(terms);
    saveSearchState();
    if (pendingRestoreScroll !== null) {
      const scrollY = pendingRestoreScroll;
      pendingRestoreScroll = null;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        window.scrollTo(0, scrollY);
        saveSearchState();
      }));
    }
  }

  function loadShard(path, generation) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      activeRequest = request;
      request.open('GET', path, true);
      request.overrideMimeType('application/json; charset=utf-8');
      request.onload = () => {
        if (activeRequest === request) activeRequest = null;
        if (generation !== searchGeneration) return reject(new Error('cancelled'));
        if (request.status !== 0 && (request.status < 200 || request.status >= 300)) {
          return reject(new Error(`HTTP ${request.status}`));
        }
        try {
          resolve(JSON.parse(request.responseText));
        } catch (_) {
          reject(new Error('索引分片格式错误'));
        }
      };
      request.onerror = () => {
        if (activeRequest === request) activeRequest = null;
        reject(new Error('索引分片读取失败'));
      };
      request.onabort = () => reject(new Error('cancelled'));
      request.send();
    });
  }

  async function render() {
    stopCurrentSearch();
    const generation = searchGeneration;
    const phrase = input.value.trim().toLocaleLowerCase();
    const terms = queryTerms(input.value);
    const collection = collectionFilter.value;
    const category = categoryFilter.value;
    const year = yearFilter.value;
    const scope = scopeText();
    const eligible = new Uint8Array(DATA.length);
    for (let index = 0; index < DATA.length; index += 1) {
      if (passesFilters(DATA[index], collection, category, year)) eligible[index] = 1;
    }

    matches = [];
    if (!terms.length) {
      for (let index = 0; index < DATA.length; index += 1) {
        if (eligible[index]) matches.push({item: DATA[index], score: score(DATA[index], '', [])});
      }
      finishResults(terms, scope);
      return;
    }

    results.innerHTML = '<div class="empty"><strong>正在检索全部正文…</strong>索引按小块读取，避免手机内存不足，请稍候。</div>';
    loadMore.hidden = true;
    for (let shardIndex = 0; shardIndex < SHARDS.length; shardIndex += 1) {
      if (generation !== searchGeneration) return;
      resultMeta.textContent =
        `${scope ? scope + '｜' : ''}正在全文检索 ${shardIndex + 1}/${SHARDS.length}…`;
      let rows;
      try {
        rows = await loadShard(SHARDS[shardIndex], generation);
      } catch (error) {
        if (error.message === 'cancelled' || generation !== searchGeneration) return;
        resultMeta.textContent = '全文索引读取失败';
        results.innerHTML =
          '<div class="empty"><strong>检索暂时失败</strong>请清除关键词后重试；若仍失败，请重新安装最新版 APK。</div>';
        return;
      }
      for (const row of rows) {
        const index = row[0];
        if (!eligible[index]) continue;
        const text = String(row[1] || '');
        const item = DATA[index];
        const meta = `${titleText(item)} ${metaText(item)}`;
        if (!terms.every(term => text.includes(term) || meta.includes(term))) continue;
        matches.push({
          item,
          score: score(item, phrase, terms),
          snippet: makeSnippet(text, terms)
        });
      }
      rows = null;
    }
    if (generation === searchGeneration) finishResults(terms, scope);
  }

  function scheduleRender() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      visible = PAGE_SIZE;
      render();
    }, 260);
  }

  function resetSearch(focusInput) {
    const wasActive = hasActiveSearch();
    stopCurrentSearch();
    clearTimeout(debounceTimer);
    input.value = '';
    collectionFilter.value = '';
    categoryFilter.value = '';
    yearFilter.value = '';
    document.querySelectorAll('.collection-card').forEach(card => {
      card.setAttribute('aria-pressed', 'false');
    });
    refreshCategoryOptions();
    visible = PAGE_SIZE;
    pendingRestoreScroll = null;
    render();
    clearSearchState();
    if (focusInput) {
      input.scrollIntoView({behavior: 'auto', block: 'center'});
      input.focus();
    }
    return wasActive;
  }

  populate(collectionFilter, DATA.map(item => item.collection_id), '', '全部资料库');
  [...collectionFilter.options].forEach(option => {
    const item = DATA.find(entry => entry.collection_id === option.value);
    if (item) option.textContent = item.collection;
  });
  populate(yearFilter, DATA.map(item => yearOf(item.date)), '', '全部年份');
  refreshCategoryOptions();

  const params = new URLSearchParams(location.search);
  const forcedHome = params.has('_home');
  if (forcedHome) clearSearchState();
  const savedState = forcedHome ? null : readSearchState();
  const requestedQuery = params.has('q') ? params.get('q') : savedState?.q;
  const requestedCollection = params.has('collection')
    ? params.get('collection')
    : savedState?.collection;
  const requestedCategory = params.has('category')
    ? params.get('category')
    : savedState?.category;
  const requestedYear = params.has('year') ? params.get('year') : savedState?.year;
  input.value = requestedQuery || '';
  if ([...collectionFilter.options].some(option => option.value === requestedCollection)) {
    collectionFilter.value = requestedCollection;
  }
  refreshCategoryOptions();
  if ([...categoryFilter.options].some(option => option.value === requestedCategory)) {
    categoryFilter.value = requestedCategory;
  }
  if ([...yearFilter.options].some(option => option.value === requestedYear)) {
    yearFilter.value = requestedYear;
  }
  if (savedState) {
    const savedVisible = Number(savedState.visible);
    if (Number.isFinite(savedVisible) && savedVisible >= PAGE_SIZE) {
      visible = Math.floor(savedVisible / PAGE_SIZE) * PAGE_SIZE;
    }
    const savedScroll = Number(savedState.scrollY);
    if (Number.isFinite(savedScroll) && savedScroll >= 0) {
      pendingRestoreScroll = savedScroll;
    }
  }

  document.querySelectorAll('.collection-card').forEach(card => {
    card.setAttribute('aria-pressed', String(card.dataset.collection === collectionFilter.value));
    card.querySelector('.collection-filter-action').addEventListener('click', () => {
      setCollection(card.getAttribute('aria-pressed') === 'true' ? '' : card.dataset.collection);
    });
  });
  input.addEventListener('input', scheduleRender);
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      clearTimeout(debounceTimer);
      visible = PAGE_SIZE;
      render();
    }
  });
  collectionFilter.addEventListener('change', () => setCollection(collectionFilter.value));
  categoryFilter.addEventListener('change', () => {
    visible = PAGE_SIZE;
    render();
  });
  yearFilter.addEventListener('change', () => {
    visible = PAGE_SIZE;
    render();
  });
  loadMore.addEventListener('click', () => {
    visible += PAGE_SIZE;
    renderResults(queryTerms(input.value));
    saveSearchState();
  });
  results.addEventListener('click', event => {
    if (event.target.closest('a.result')) saveSearchState();
  });
  window.addEventListener('pagehide', saveSearchState);
  document.getElementById('resetAll').addEventListener('click', () => resetSearch(true));

  // Native Android toolbar/back actions call these directly. No file:// History API is used.
  window.KB_RESET_SEARCH = () => resetSearch(true);
  window.KB_HAS_ACTIVE_SEARCH = () => hasActiveSearch();
  render();
})();
