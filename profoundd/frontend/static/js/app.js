/* Profoundd - Main JavaScript */

document.addEventListener('DOMContentLoaded', function() {

    // --- Search Suggestions (Autocomplete) ---
    var searchInputs = document.querySelectorAll('.search-input, .nav-search-input');
    searchInputs.forEach(function(input) {
        var debounceTimer;
        var suggestBox = document.createElement('div');
        suggestBox.className = 'suggestions-dropdown';
        suggestBox.style.display = 'none';
        input.parentNode.style.position = 'relative';
        input.parentNode.appendChild(suggestBox);

        input.addEventListener('input', function() {
            var query = this.value.trim();
            clearTimeout(debounceTimer);

            if (query.length < 2) {
                suggestBox.style.display = 'none';
                return;
            }

            debounceTimer = setTimeout(function() {
                fetch('/api/suggest?q=' + encodeURIComponent(query))
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (!data.suggestions || data.suggestions.length === 0) {
                            suggestBox.style.display = 'none';
                            return;
                        }
                        suggestBox.innerHTML = data.suggestions.map(function(s) {
                            return '<a class="suggestion-item" href="/search?q=' +
                                encodeURIComponent(s.title) + '">' +
                                '<span class="suggestion-title">' + escapeHtml(s.title) + '</span>' +
                                '<span class="suggestion-meta">' + escapeHtml(s.source) + ' &middot; ' + escapeHtml(s.category) + '</span>' +
                                '</a>';
                        }).join('');
                        suggestBox.style.display = 'block';
                    })
                    .catch(function() { suggestBox.style.display = 'none'; });
            }, 200);
        });

        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                suggestBox.style.display = 'none';
                this.closest('form').submit();
            }
        });

        // Hide on click outside
        document.addEventListener('click', function(e) {
            if (!input.contains(e.target) && !suggestBox.contains(e.target)) {
                suggestBox.style.display = 'none';
            }
        });
    });

    // --- Auto-dismiss alerts after 5 seconds ---
    var alerts = document.querySelectorAll('.alert');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.3s';
            setTimeout(function() { alert.remove(); }, 300);
        }, 5000);
    });

    // --- Keyboard shortcut: '/' to focus search ---
    document.addEventListener('keydown', function(e) {
        if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
            var active = document.activeElement;
            if (active.tagName !== 'INPUT' && active.tagName !== 'TEXTAREA') {
                e.preventDefault();
                var si = document.querySelector('.search-input') || document.querySelector('.nav-search-input');
                if (si) si.focus();
            }
        }
    });

    // --- Relative Time Dates ---
    var dateElements = document.querySelectorAll('[data-time]');
    dateElements.forEach(function(el) {
        var dt = el.getAttribute('data-time');
        if (dt) {
            el.textContent = timeAgo(dt);
            el.title = new Date(dt).toLocaleString();
        }
    });

    // Also convert result dates
    var resultDates = document.querySelectorAll('.result-date');
    resultDates.forEach(function(el) {
        var text = el.textContent.trim();
        if (text && text.match(/^\d{4}-\d{2}-\d{2}/)) {
            el.title = text;
            el.textContent = timeAgo(text);
        }
    });
});

// --- Utility Functions ---

function timeAgo(dateStr) {
    var date = new Date(dateStr);
    var now = new Date();
    var seconds = Math.floor((now - date) / 1000);

    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
    if (seconds < 604800) return Math.floor(seconds / 86400) + 'd ago';
    if (seconds < 2592000) return Math.floor(seconds / 604800) + 'w ago';
    return dateStr.substring(0, 10);
}

function escapeHtml(text) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
}
