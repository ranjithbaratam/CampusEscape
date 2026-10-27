document.addEventListener('keydown', event => {
    if (!event.altKey || event.ctrlKey || event.metaKey) return;
    const key = event.key.toLowerCase();
    if (key === 'm') { event.preventDefault(); location.assign('/map'); }
    if (key === 'd') { event.preventDefault(); location.assign('/dashboard'); }
});
