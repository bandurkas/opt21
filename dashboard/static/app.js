function formatCurrency(value) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2
    }).format(value);
}

function formatPnL(value) {
    const formatted = formatCurrency(Math.abs(value));
    return value >= 0 ? `+${formatted}` : `-${formatted}`;
}

function getPnLClass(value) {
    return value >= 0 ? 'pnl-positive' : 'pnl-negative';
}

async function updateDashboard() {
    try {
        const response = await fetch('/api/data');
        const data = await response.json();

        if (data.error) {
            console.error(data.error);
            return;
        }

        // Update Balances
        document.getElementById('total_equity').textContent = formatCurrency(data.total_equity);
        document.getElementById('aevo_balance').textContent = formatCurrency(data.aevo_balance);
        document.getElementById('deri_balance').textContent = formatCurrency(data.deri_balance);
        
        const floatingEl = document.getElementById('floating_pnl');
        floatingEl.textContent = formatPnL(data.floating_pnl);
        floatingEl.className = `value ${getPnLClass(data.floating_pnl)}`;

        // Update Open Trades
        const openTbody = document.querySelector('#open_trades_table tbody');
        openTbody.innerHTML = '';
        if (data.open_trades.length === 0) {
            openTbody.innerHTML = '<tr><td colspan="7" class="empty-state">No active positions</td></tr>';
        } else {
            data.open_trades.forEach(trade => {
                const tr = document.createElement('tr');
                const entryGap = Math.abs(trade.entry_aevo_mid - trade.entry_deri_mid);
                tr.innerHTML = `
                    <td>#${trade.trade_id}</td>
                    <td>${trade.time_str}</td>
                    <td>${trade.pair}</td>
                    <td>${trade.trade_size} ETH</td>
                    <td>${formatCurrency(entryGap)}</td>
                    <td>${formatCurrency(trade.current_gap)}</td>
                    <td class="${getPnLClass(trade.floating_pnl)}">${formatPnL(trade.floating_pnl)}</td>
                `;
                openTbody.appendChild(tr);
            });
        }

        // Update Closed Trades
        const closedTbody = document.querySelector('#closed_trades_table tbody');
        closedTbody.innerHTML = '';
        if (data.closed_trades.length === 0) {
            closedTbody.innerHTML = '<tr><td colspan="5" class="empty-state">No closed trades yet</td></tr>';
        } else {
            data.closed_trades.forEach(trade => {
                const tr = document.createElement('tr');
                const pnl = trade.actual_pnl || 0.0;
                tr.innerHTML = `
                    <td>#${trade.trade_id}</td>
                    <td>${trade.time_str}</td>
                    <td>${trade.pair}</td>
                    <td>${trade.trade_size} ETH</td>
                    <td class="${getPnLClass(pnl)}">${formatPnL(pnl)}</td>
                `;
                closedTbody.appendChild(tr);
            });
        }

    } catch (error) {
        console.error('Error fetching dashboard data:', error);
    }
}

// Initial update and set interval
updateDashboard();
setInterval(updateDashboard, 3000);
