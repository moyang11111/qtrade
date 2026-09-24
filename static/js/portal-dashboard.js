/* QTrade data renderer for the original portal layout. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const make = (tag, className = '', value = '') => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(value);
    return node;
  };
  const number = (value) => Number(value || 0).toLocaleString('zh-CN');
  function replace(id, ...children) { const node = $(id); if (node) node.replaceChildren(...children); }
  function unavailable(id, title, reason) {
    const heading = make('h2', '', title);
    const body = make('div', 'mini', reason);
    body.style.marginTop = '10px';
    body.style.color = '#a1a1aa';
    replace(id, heading, body);
  }
  function card(label, value, detail) {
    const article = make('div', 'pt-k anim-hover-lift');
    const top = make('div', 'pt-k-top');
    top.appendChild(make('span', 'pt-k-lb', label));
    const amount = make('div', 'pt-k-vl', number(value));
    const date = make('div', 'pt-k-dt', detail);
    article.append(top, amount, date);
    return article;
  }
  function row(text) { return make('div', 'brief-item', text); }
  function render(snapshot) {
    const date = snapshot.target_date;
    const actions = snapshot.actions || {};
    const summary = make('span', 'tm', `已验证收盘快照 ${date}`);
    replace('summary-box', summary, make('span', 'mini', `覆盖 ${number(snapshot.total)} 只 · 有效因子 ${number(snapshot.valid_count)} 只 · 所有统计来自同一快照`));

    replace('kpi-box',
      card('覆盖股票', snapshot.total, `数据日 ${date}`),
      card('可交易', snapshot.tradable_count, `数据日 ${date}`),
      card('有效因子', snapshot.valid_count, `数据日 ${date}`),
      card('评分偏强', actions.buy, `研究评分 · ${date}`),
      card('评分偏弱', actions.sell, `研究评分 · ${date}`),
    );
    unavailable('timing-box', '市场择时', '当前已验证快照不含择时模型结果。');
    unavailable('traffic-light-box', '择时趋势', '当前已验证快照不含指数均线和趋势计结果。');
    $('wufu-body').textContent = '当前已验证快照不含全球 ETF 轮动结果。';
    $('rt-box').textContent = '此处展示收盘快照；盘中实时行情请查看“行情”页面。';
    $('global-rot-box').style.display = 'none';

    $('brief-tag').textContent = `数据日 ${date}`;
    const leaders = (snapshot.leaders || []).slice(0, 3);
    const topText = leaders.length
      ? `综合分数前三：${leaders.map(item => `${item.name || item.symbol} ${Number(item.score).toFixed(2)}`).join('、')}`
      : '暂无有效评分';
    replace('brief-box',
      row(`本次覆盖 ${number(snapshot.total)} 只，可交易 ${number(snapshot.tradable_count)} 只。`),
      row(`有效综合评分 ${number(snapshot.valid_count)} 只；偏强 ${number(actions.buy)} 只，中性 ${number(actions.hold)} 只，偏弱 ${number(actions.sell)} 只。`),
      row(topText),
      row('评分方向仅供研究，不代表 Pitch 审批或交易指令。'),
    );
    replace('sys-box',
      row(`当前完整交易日：${date}`),
      row(`QTrade 快照：${snapshot.generation.slice(0, 12)}`),
      row('行情历史、因子、评分方向和同步结果已通过同一代快照校验。'),
    );
    $('api-status-tag').textContent = '快照已验证';
    replace('api-box', row('QTrade 快照接口正常'), row(`数据日 ${date} · 覆盖 ${number(snapshot.total)} 只`));
    $('chain-tag').textContent = `4 环节 · ${date}`;
    replace('chain-box',
      row(`行情历史与股票资料 · ${date} · 已验证`),
      row(`因子计算 · ${date} · 已验证`),
      row(`评分方向 · ${date} · 已验证`),
      row(`快照同步 · ${date} · 已验证`),
    );
    const button = $('chain-refresh-btn');
    button.textContent = '更新全域数据';
    button.onclick = () => window.parent.postMessage({type: 'qtrade:portal-open-control'}, window.location.origin);
    const note = button.nextElementSibling;
    if (note) note.textContent = '前往“数据与运行”使用 QTrade 更新按钮和进度条';
    const progress = $('chain-progress');
    if (progress) progress.style.display = 'none';
  }
  async function load() {
    try {
      const response = await fetch('/api/research/snapshot?view=portal', {cache: 'no-store'});
      if (!response.ok) throw new Error(response.status === 503 ? '尚无已验证快照，请先完成数据更新。' : `HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      replace('summary-box', make('span', 'tm', '门户快照暂不可用'), make('span', 'mini', error.message));
      for (const [id, title] of [['timing-box', '市场择时'], ['traffic-light-box', '择时趋势']]) {
        unavailable(id, title, '等待 QTrade 已验证快照。');
      }
      $('wufu-body').textContent = '等待 QTrade 已验证快照。';
      $('rt-box').textContent = '等待 QTrade 已验证快照。';
      replace('kpi-box');
      replace('brief-box', row('等待 QTrade 已验证快照。'));
      replace('sys-box', row('等待 QTrade 已验证快照。'));
      replace('api-box', row('等待 QTrade 已验证快照。'));
      replace('chain-box', row('等待 QTrade 已验证快照。'));
    }
  }
  void load();
  window.setInterval(load, 60000);
})();
