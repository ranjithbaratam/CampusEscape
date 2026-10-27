(() => {
    const body = document.body;
    const rail = document.getElementById('campusRail');
    const scrim = document.getElementById('campusRailScrim');
    const toggle = document.getElementById('campusNavToggle');
    if (!rail || !toggle) return;

    body.classList.add('app-framed');
    const mobile = window.matchMedia('(max-width: 720px)');

    const closeMobileRail = () => {
        rail.classList.remove('is-open');
        scrim.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
        body.classList.remove('rail-open');
    };

    toggle.addEventListener('click', () => {
        if (mobile.matches) {
            const isOpen = rail.classList.toggle('is-open');
            scrim.hidden = !isOpen;
            toggle.setAttribute('aria-expanded', String(isOpen));
            body.classList.toggle('rail-open', isOpen);
            if (isOpen) rail.querySelector('a')?.focus();
            return;
        }

        const collapsed = body.classList.toggle('rail-collapsed');
        toggle.setAttribute('aria-expanded', String(!collapsed));
        try { localStorage.setItem('campusEscapeNavCollapsed', String(collapsed)); } catch (error) { /* Keep the in-page state. */ }
    });

    try {
        if (localStorage.getItem('campusEscapeNavCollapsed') === 'true' && !mobile.matches) {
            body.classList.add('rail-collapsed');
            toggle.setAttribute('aria-expanded', 'false');
        }
    } catch (error) { /* Use the expanded navigation by default. */ }

    scrim.addEventListener('click', closeMobileRail);
    rail.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
        if (mobile.matches) closeMobileRail();
    }));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && rail.classList.contains('is-open')) {
            closeMobileRail();
            toggle.focus();
        }
    });
    mobile.addEventListener('change', () => {
        closeMobileRail();
        body.classList.remove('rail-collapsed');
    });
})();
