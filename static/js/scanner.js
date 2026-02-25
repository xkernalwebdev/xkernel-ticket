const statusPill = document.getElementById('status-pill');
const statusText = document.getElementById('status-text');
const nameEl = document.getElementById('detail-name');
const eventEl = document.getElementById('detail-event');
const ticketEl = document.getElementById('detail-ticket');
const extraEl = document.getElementById('detail-extra');

function setStatus(mode, text) {
    statusPill.className = 'status-pill ' + mode;
    statusText.textContent = text;
}

function setDetails(name, event, ticket, extra) {
    nameEl.textContent = name || '—';
    eventEl.textContent = event || '—';
    ticketEl.textContent = ticket || '—';
    extraEl.textContent = extra || '';
}

let lastText = null;
let lastTime = 0;

function onScanSuccess(decodedText, decodedResult) {
    const now = Date.now();
    // Prevent hammering backend with same value many times per second
    if (decodedText === lastText && now - lastTime < 1500) {
        return;
    }
    lastText = decodedText;
    lastTime = now;

    setStatus('idle', 'Checking ticket...');
    setDetails('—', '—', '—', 'Verifying QR with server...');

    fetch('/verify', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ ticket_data: decodedText })
    })
    .then(async (r) => {
        const data = await r.json();
        if (r.ok && data.valid) {
            setStatus('valid', 'Valid Ticket - Entry Granted');
            setDetails(data.name, data.event, data.ticket_id, 'Marked as used in database.');
        } else {
            // 400 or non-valid
            const msg = data.message || 'Invalid ticket';
            if (msg.toLowerCase().includes('already')) {
                setStatus('used', 'Already Used Ticket');
                setDetails(data.name || '—', data.event || '—', data.ticket_id || '—', data.scanned_at ? `Used at: ${data.scanned_at}` : msg);
            } else if (msg.toLowerCase().includes('invalid')) {
                setStatus('invalid', 'Invalid Ticket');
                setDetails('—', '—', '—', msg);
            } else {
                setStatus('invalid', 'Error');
                setDetails('—', '—', '—', msg);
            }
        }
    })
    .catch(err => {
        setStatus('invalid', 'Network error');
        setDetails('—', '—', '—', 'Could not reach server.');
        console.error(err);
    });
}

const html5QrCode = new Html5Qrcode("reader");

html5QrCode.start(
    { facingMode: "environment" },
    { fps: 10, qrbox: { width: 250, height: 250 } },
    onScanSuccess
).catch(err => {
    console.error('Camera start error:', err);
    setStatus('invalid', 'Cannot access camera');
    setDetails('—', '—', '—', 'Check browser camera permissions.');
});
