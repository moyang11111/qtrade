(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const view = new URLSearchParams(location.search).get('view') === 'decisions' ? 'decisions' : 'factors';
  const isDecision = view === 'decisions';
  const label = isDecision ? '决策评分' : '因子仪表';
  $('title').textContent = label;
  $('list-title').textContent = isDecision ? '评分方向明细' : '因子计算结果';
  $('description').textContent = isDecision ? '基于 QTrade 综合因子分数的研究方向，按最近一次成功更新的交易日展示。' : '按最近一次成功更新的交易日展示各股票的因子与综合分数。';
  $('footer-note').textContent = isDecision ? '评分方向由分数阈值生成，仅供研究；不代表 Pitch 审批结果或交易指令。' : '因子值来自 QTrade 快照；因子库的 ICIR 等指标属于另一套研究数据。';

  function cell(row, value, className) {
    const td = document.createElement('td');
    td.textContent = value == null || value === '' ? '—' : String(value);
    if (className) td.className = className;
    row.appendChild(td);
    return td;
  }
  function score(value) { return typeof value === 'number' ? value.toFixed(3) : '—'; }
  function render(data) {
    $('date').textContent = data.target_date;
    $('generation').textContent = `快照 ${data.generation.slice(0, 12)}`;
    $('total').textContent = data.total.toLocaleString('zh-CN');
    $('valid').textContent = data.valid_count.toLocaleString('zh-CN');
    $('candidates').textContent = data.candidate_count.toLocaleString('zh-CN');
    $('note').textContent = $('symbol').value ? `股票 ${$('symbol').value} 的快照结果` : '默认展示评分最高的 100 只股票';
    const head = document.createElement('tr');
    (isDecision ? ['代码', '名称', '综合分数', '评分方向', '交易日'] : ['代码', '名称', '综合分数', '交易日', '因子明细']).forEach((name) => {
      const th = document.createElement('th'); th.textContent = name; head.appendChild(th);
    });
    $('columns').replaceChildren(head);
    const rows = document.createDocumentFragment();
    for (const item of data.records) {
      const tr = document.createElement('tr');
      cell(tr, item.symbol); cell(tr, item.name); cell(tr, score(item.score), 'score');
      if (isDecision) {
        const directions = {buy: ['偏强', 'action-up'], sell: ['偏弱', 'action-down'], hold: ['中性', 'action-flat']};
        const direction = directions[item.action] || ['—', ''];
        cell(tr, direction[0], direction[1]); cell(tr, item.as_of);
      } else {
        cell(tr, item.as_of);
        const td = document.createElement('td');
        const details = document.createElement('details');
        const summary = document.createElement('summary'); summary.textContent = '查看因子'; details.appendChild(summary);
        const list = document.createElement('dl');
        for (const [key, value] of Object.entries(item.values || {})) {
          const pair = document.createElement('div');
          const term = document.createElement('dt'); term.textContent = key;
          const definition = document.createElement('dd'); definition.textContent = score(value);
          pair.append(term, definition); list.appendChild(pair);
        }
        details.appendChild(list); td.appendChild(details); tr.appendChild(td);
      }
      rows.appendChild(tr);
    }
    $('rows').replaceChildren(rows);
    $('message').textContent = data.records.length ? '' : '该股票不在本次快照中。';
  }
  async function load() {
    const symbol = $('symbol').value.trim();
    if (symbol && !/^\d{6}$/.test(symbol)) { $('message').textContent = '请输入 6 位股票代码。'; return; }
    $('message').textContent = '正在读取已验证快照…';
    try {
      const params = new URLSearchParams({view});
      if (symbol) params.set('symbol', symbol);
      const response = await fetch(`/api/research/snapshot?${params}`, {cache: 'no-store'});
      if (!response.ok) throw new Error(response.status === 503 ? '尚无已验证的研究快照，请先完成数据更新。' : `读取失败（HTTP ${response.status}）。`);
      render(await response.json());
    } catch (error) { $('message').textContent = error.message; }
  }
  $('search-form').addEventListener('submit', (event) => { event.preventDefault(); void load(); });
  $('clear').addEventListener('click', () => { $('symbol').value = ''; void load(); });
  void load();
})();
