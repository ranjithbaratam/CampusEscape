(() => {
    const key = 'campusEscapeTheme';
    const apply = theme => {
        document.documentElement.dataset.theme = theme;
        document.body.dataset.theme = theme;
        document.querySelectorAll('[data-theme-toggle]').forEach(button => {
            button.textContent = theme === 'light' ? '☀️ Light' : '🌙 Dark';
            button.setAttribute('aria-pressed', String(theme === 'light'));
        });
    };
    let savedTheme = 'dark';
    try { savedTheme = localStorage.getItem(key) === 'light' ? 'light' : 'dark'; } catch (error) { /* Theme remains available for this page. */ }
    apply(savedTheme);
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-theme-toggle]');
        if (!button) return;
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        try { localStorage.setItem(key, next); } catch (error) { /* Keep the selected theme in memory. */ }
        apply(next);
    });
})();
