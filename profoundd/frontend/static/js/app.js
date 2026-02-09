/* Profoundd - Main JavaScript */

document.addEventListener('DOMContentLoaded', function() {
    // Auto-submit search on Enter
    const searchInputs = document.querySelectorAll('.search-input, .nav-search-input');
    searchInputs.forEach(function(input) {
        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                this.closest('form').submit();
            }
        });
    });

    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.3s';
            setTimeout(function() { alert.remove(); }, 300);
        }, 5000);
    });

    // Keyboard shortcut: press '/' to focus search
    document.addEventListener('keydown', function(e) {
        if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
            var active = document.activeElement;
            if (active.tagName !== 'INPUT' && active.tagName !== 'TEXTAREA') {
                e.preventDefault();
                var searchInput = document.querySelector('.search-input') || document.querySelector('.nav-search-input');
                if (searchInput) searchInput.focus();
            }
        }
    });
});
