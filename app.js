let currentPeriod = 'recent';
let liveChart = null;
let pollTimer = null;
let currentPrice = 0.35;

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    fetchTelemetry();
    fetchHistoryData(currentPeriod);

    // Poll telemetry every 1500 ms
    pollTimer = setInterval(fetchTelemetry, 1500);

    setupEventListeners();
});

function initChart() {
    const ctx = document.getElementById('liveChart').getContext('2d');
    
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.font.family = 'Outfit, sans-serif';

    liveChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Gesamt (W)',
                    data: [],
                    borderColor: '#00f2fe',
                    backgroundColor: 'rgba(0, 242, 254, 0.08)',
                    fill: true,
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 2
                },
                {
                    label: 'GPU RTX 3060 (W)',
                    data: [],
                    borderColor: '#8b5cf6',
                    backgroundColor: 'transparent',
                    borderWidth: 1.5,
                    pointRadius: 0
                },
                {
                    label: 'CPU (W)',
                    data: [],
                    borderColor: '#4facfe',
                    backgroundColor: 'transparent',
                    borderWidth: 1.5,
                    pointRadius: 0
                },
                {
                    label: 'Monitor (W)',
                    data: [],
                    borderColor: '#f59e0b',
                    backgroundColor: 'transparent',
                    borderWidth: 1.5,
                    pointRadius: 0
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 300 },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.06)' },
                    suggestedMin: 0,
                    suggestedMax: 200,
                    title: { display: true, text: 'Leistungsaufnahme (Watt)' }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { boxWidth: 12, usePointStyle: true }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(10, 14, 26, 0.9)',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1
                }
            }
        }
    });
}

async function fetchTelemetry() {
    try {
        const response = await fetch('/api/telemetry');
        if (!response.ok) return;
        const data = await response.json();

        // Update UI tiles
        document.getElementById('val-total-w').textContent = data.total_w.toFixed(1);
        document.getElementById('val-cpu-w').textContent = data.cpu_w.toFixed(1);
        document.getElementById('val-gpu-w').textContent = data.gpu_w.toFixed(1);
        document.getElementById('val-mon-w').textContent = data.monitor_w.toFixed(1);

        if (data.connected_monitors && data.connected_monitors.length > 0) {
            document.getElementById('mon-detected-names').textContent = data.connected_monitors.join(', ');
        }

        document.getElementById('val-today-kwh').textContent = data.today_kwh.toFixed(4);
        document.getElementById('val-today-cost').textContent = data.today_cost.toFixed(2);
        document.getElementById('val-all-kwh').textContent = data.all_kwh.toFixed(4);
        document.getElementById('val-all-cost').textContent = data.all_cost.toFixed(2);

        // Calculate current cost forecast per hour & per day
        currentPrice = parseFloat(data.settings?.electricity_price || 0.35);
        const kw = data.total_w / 1000.0;
        const costPerHour = kw * currentPrice;
        const costPerDay = costPerHour * 24.0;

        document.getElementById('val-cost-hour').textContent = costPerHour.toFixed(3);
        document.getElementById('val-cost-day').textContent = costPerDay.toFixed(2);

        // Update meter bars
        const maxWattEstimate = 350.0;
        const totalPct = Math.min(100, (data.total_w / maxWattEstimate) * 100);
        document.getElementById('total-meter-fill').style.width = `${totalPct}%`;

        document.getElementById('cpu-bar-fill').style.width = `${Math.min(100, (data.cpu_w / 120) * 100)}%`;
        document.getElementById('gpu-bar-fill').style.width = `${Math.min(100, (data.gpu_w / 170) * 100)}%`;
        document.getElementById('mon-bar-fill').style.width = `${Math.min(100, (data.monitor_w / 50) * 100)}%`;

        // Update logging badge & Donate button
        const badge = document.getElementById('logging-badge');
        const badgeText = document.getElementById('logging-status-text');
        const donateBtn = document.getElementById('donate-btn');

        if (data.settings && data.settings.donate_url && data.settings.donate_url.trim() !== '') {
            donateBtn.href = data.settings.donate_url;
            donateBtn.style.display = 'inline-flex';
        } else {
            donateBtn.style.display = 'none';
        }

        if (data.is_logging) {
            badge.style.opacity = '1';
            badgeText.textContent = 'Protokollierung aktiv';
        } else {
            badge.style.opacity = '0.5';
            badgeText.textContent = 'Protokollierung pausiert';
        }

        // If in "recent" chart mode, append live telemetry point
        if (currentPeriod === 'recent') {
            const timeStr = new Date().toLocaleTimeString('de-DE');
            appendLivePoint(timeStr, data.total_w, data.gpu_w, data.cpu_w, data.monitor_w);
        }
    } catch (e) {
        console.error("Telemetry fetch error", e);
    }
}

function appendLivePoint(label, total, gpu, cpu, mon) {
    if (!liveChart) return;
    const labels = liveChart.data.labels;
    const ds = liveChart.data.datasets;

    labels.push(label);
    ds[0].data.push(total);
    ds[1].data.push(gpu);
    ds[2].data.push(cpu);
    ds[3].data.push(mon);

    if (labels.length > 40) {
        labels.shift();
        ds[0].data.shift();
        ds[1].data.shift();
        ds[2].data.shift();
        ds[3].data.shift();
    }
    liveChart.update('none');
}

async function fetchHistoryData(period) {
    try {
        const res = await fetch(`/api/history?period=${period}`);
        if (!res.ok) return;
        const rows = await res.json();

        if (!liveChart) return;

        if (period === 'recent') {
            liveChart.config.type = 'line';
            liveChart.data.labels = rows.map(r => r.time_label);
            liveChart.data.datasets[0].data = rows.map(r => r.total_w);
            liveChart.data.datasets[1].data = rows.map(r => r.gpu_w);
            liveChart.data.datasets[2].data = rows.map(r => r.cpu_w);
            liveChart.data.datasets[3].data = rows.map(r => r.monitor_w);
        } else if (period === 'today') {
            liveChart.config.type = 'bar';
            liveChart.data.labels = rows.map(r => r.time_label);
            liveChart.data.datasets[0].data = rows.map(r => r.avg_total_w);
            liveChart.data.datasets[1].data = rows.map(r => r.avg_gpu_w);
            liveChart.data.datasets[2].data = rows.map(r => r.avg_cpu_w);
            liveChart.data.datasets[3].data = [];
        } else if (period === 'daily') {
            liveChart.config.type = 'bar';
            liveChart.data.labels = rows.map(r => r.time_label);
            liveChart.data.datasets[0].data = rows.map(r => r.avg_total_w);
            liveChart.data.datasets[1].data = [];
            liveChart.data.datasets[2].data = [];
            liveChart.data.datasets[3].data = [];
        }
        liveChart.update();
    } catch (e) {
        console.error("History fetch error", e);
    }
}

function setupEventListeners() {
    // Chart Tab Buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentPeriod = e.target.getAttribute('data-period');
            fetchHistoryData(currentPeriod);
        });
    });

    // CSV Export
    document.getElementById('export-csv-btn').addEventListener('click', () => {
        window.location.href = '/api/export';
    });

    // Donate Button Click
    const donateBtn = document.getElementById('donate-btn');
    if (donateBtn) {
        donateBtn.addEventListener('click', () => {
            donateBtn.textContent = '❤️ Vielen Dank!';
            setTimeout(() => {
                donateBtn.style.opacity = '0.5';
            }, 3000);
        });
    }

    // Modal Settings
    const modal = document.getElementById('settings-modal');
    document.getElementById('open-settings-btn').addEventListener('click', async () => {
        const res = await fetch('/api/telemetry');
        const data = await res.json();
        const s = data.settings || {};

        document.getElementById('input-price').value = s.electricity_price || 0.35;
        document.getElementById('input-mon-w').value = s.monitor_wattage || 'auto';
        document.getElementById('input-cpu-tdp').value = s.cpu_tdp || 95;
        document.getElementById('input-psu-eff').value = s.psu_efficiency || 88;
        document.getElementById('input-interval').value = s.logging_interval || 2.0;
        document.getElementById('input-donate-url').value = s.donate_url || 'https://ko-fi.com';
        document.getElementById('input-is-logging').checked = s.is_logging === 'true';

        modal.classList.add('active');
    });

    document.getElementById('close-settings-btn').addEventListener('click', () => modal.classList.remove('active'));
    document.getElementById('cancel-settings-btn').addEventListener('click', () => modal.classList.remove('active'));

    document.getElementById('save-settings-btn').addEventListener('click', async () => {
        const payload = {
            electricity_price: document.getElementById('input-price').value,
            monitor_wattage: document.getElementById('input-mon-w').value,
            cpu_tdp: document.getElementById('input-cpu-tdp').value,
            psu_efficiency: document.getElementById('input-psu-eff').value,
            logging_interval: document.getElementById('input-interval').value,
            donate_url: document.getElementById('input-donate-url').value,
            is_logging: document.getElementById('input-is-logging').checked ? 'true' : 'false'
        };

        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        modal.classList.remove('active');
        fetchTelemetry();
    });

    document.getElementById('reset-logs-btn').addEventListener('click', async () => {
        if (confirm("Möchtest du wirklich alle aufgezeichneten Verbrauchs-Protokolle löschen?")) {
            await fetch('/api/reset', { method: 'POST' });
            modal.classList.remove('active');
            fetchTelemetry();
            fetchHistoryData(currentPeriod);
        }
    });
}
