async function verifyKey(event) {
    event.preventDefault();
    const input = document.getElementById('keyInput');
    const modal = document.getElementById('responseModal');
    const card = document.getElementById('modalCard');
    const icon = document.getElementById('modalIcon');
    const title = document.getElementById('modalTitle');
    const desc = document.getElementById('modalDesc');
    const btn = document.getElementById('modalBtn');
    const submit = document.querySelector('.btn-unlock');
    submit.disabled = true;
    try {
        const response = await fetch('/api/vault/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: input.value.trim() })
        });
        const result = await response.json();
        const success = response.ok && result.success;
        card.className = success ? 'modal-card success' : 'modal-card err';
        icon.innerText = success ? '🎉' : '⚠️';
        title.innerText = success ? 'ACCESS GRANTED!' : 'CIPHER NOT ACCEPTED';
        desc.textContent = result.message || 'The vault could not verify that key.';
        btn.className = success ? 'btn-modal success' : 'btn-modal err';
        btn.innerText = success ? 'CLAIM CERTIFICATE →' : 'TRY AGAIN';
        btn.onclick = () => { window.location.href = success ? '/certificate' : '/lock'; };
        modal.style.display = 'flex';
        if (typeof playSfx === 'function') playSfx(success ? 'correct' : 'wrong');
        if (success && typeof window.celebrateMilestone === 'function') window.celebrateMilestone();
    } catch (error) {
        card.className = 'modal-card err';
        icon.innerText = '⚠️';
        title.innerText = 'SERVER UNAVAILABLE';
        desc.textContent = 'Could not contact the vault. Check that the app is running and try again.';
        btn.innerText = 'TRY AGAIN';
        btn.onclick = closeModal;
        modal.style.display = 'flex';
    } finally {
        submit.disabled = false;
    }
}

function closeModal() {
    document.getElementById('responseModal').style.display = 'none';
    const input = document.getElementById('keyInput');
    input.value = '';
    input.focus();
}
