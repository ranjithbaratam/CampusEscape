(() => {
    const key = 'campusEscapeSkin';
    let active = 'default';
    try { active = localStorage.getItem(key) || 'default'; } catch (error) { /* Local-only fallback. */ }
    const apply = skin => {
        document.documentElement.dataset.skin = skin;
        document.querySelectorAll('[data-skin]').forEach(button => {
            const unlocked = button.dataset.unlocked === 'true';
            button.disabled = !unlocked;
            button.setAttribute('aria-pressed', String(button.dataset.skin === skin));
            button.title = unlocked ? 'Select this unlocked skin' : 'Complete the milestone shown to unlock this skin';
        });
    };
    apply(active);
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-skin]');
        if (!button || button.dataset.unlocked !== 'true') return;
        active = button.dataset.skin;
        try { localStorage.setItem(key, active); } catch (error) { /* Selection remains active for this visit. */ }
        apply(active);
        const status = document.getElementById('skinStatus');
        if (status) status.textContent = `${active[0].toUpperCase()}${active.slice(1)} skin equipped.`;
    });
})();
