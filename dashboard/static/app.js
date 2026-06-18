function fmtDays(d) {
    if (d == null) return '—';
    if (d < 1) return `${Math.round(d * 24)}h`;
    return `${d.toFixed(1)}d`;
}
function fmtInt(n) { return n == null ? '—' : new Intl.NumberFormat('en-US').format(n); }
function pctClass(v) { return v >= 0 ? 'pnl-positive' : 'pnl-negative'; }

async function update() {
    try {
        const r = await fetch('/api/status');
        const d = await r.json();
        if (d.error) { document.getElementById('status_text').textContent = d.error; return; }

        // status pill
        const pill = document.getElementById('status_pill');
        document.getElementById('status_text').textContent = d.live ? 'COLLECTING · LIVE' : 'STALLED — no recent data';
        pill.className = 'status-pill ' + (d.live ? 'ok' : 'bad');

        // collection cards
        const col = d.collection;
        document.getElementById('elapsed').textContent = fmtDays(col.elapsed_days);
        document.getElementById('cycles').textContent = fmtInt(col.cycles);
        document.getElementById('coverage').textContent = col.coverage_pct + '%';
        const total = (d.counts.iv_snapshots || 0) + (d.counts.bybit_flow || 0) + (d.counts.bybit_oi_strikes || 0);
        document.getElementById('datapoints').textContent = fmtInt(total);

        // hypotheses progress bars
        const list = document.getElementById('hyp_list');
        list.innerHTML = '';
        d.hypotheses.forEach(h => {
            const div = document.createElement('div');
            div.className = 'hyp';
            div.innerHTML = `
                <div class="hyp-head">
                    <span class="hyp-name"><b>${h.id}</b> · ${h.name}</span>
                    <span class="hyp-pct ${h.ready ? 'pnl-positive' : ''}">${h.progress_pct}%</span>
                </div>
                <div class="bar"><div class="bar-fill ${h.ready ? 'done' : ''}" style="width:${h.progress_pct}%"></div></div>
                <div class="hyp-meta">
                    <span>${h.ready ? 'Ready for backtest ✓' : `~${fmtDays(h.days_left)} left · ETA ${h.eta || '—'}`}</span>
                    <span class="needs">${h.needs}</span>
                </div>`;
            list.appendChild(div);
        });

        // market snapshot
        const m = d.market;
        document.getElementById('spot').textContent = m.spot ? '$' + fmtInt(m.spot) : '—';
        const rvEl = document.getElementById('rv');
        if (m.rv_24h != null) { rvEl.textContent = m.rv_24h + '%'; rvEl.className = 'value'; }
        else { rvEl.textContent = 'accumulating…'; rvEl.className = 'value pending'; }
        const vrpEl = document.getElementById('vrp');
        if (m.vrp != null) { vrpEl.textContent = (m.vrp >= 0 ? '+' : '') + m.vrp + ' pp'; vrpEl.className = 'value ' + pctClass(m.vrp); }
        else { vrpEl.textContent = 'accumulating…'; vrpEl.className = 'value pending'; }
        document.getElementById('oi').textContent = fmtInt(m.bybit.total_oi);
        document.getElementById('pcr').textContent = m.bybit.pcr_oi ?? '—';
        const imbEl = document.getElementById('imb');
        if (m.bybit.book_imb != null) { imbEl.textContent = m.bybit.book_imb; imbEl.className = 'value ' + pctClass(m.bybit.book_imb); }
        else imbEl.textContent = '—';

        // term structure
        const tb = document.querySelector('#term_table tbody');
        tb.innerHTML = '';
        if (!m.term.length) tb.innerHTML = '<tr><td colspan="4" class="empty-state">No snapshots yet</td></tr>';
        m.term.forEach(t => {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${t.tenor}d</td><td>${t.atm_iv ?? '—'}%</td>
                <td class="${t.rr < 0 ? 'pnl-negative' : 'pnl-positive'}">${t.rr ?? '—'} pp</td>
                <td>${t.fly ?? '—'} pp</td>`;
            tb.appendChild(tr);
        });

        document.getElementById('foot').textContent =
            `Last update ${d.last_update || '—'} · started ${col.start || '—'} · ${col.cycle_min}-min cadence`;
    } catch (e) {
        console.error(e);
    }
}

update();
setInterval(update, 15000);
