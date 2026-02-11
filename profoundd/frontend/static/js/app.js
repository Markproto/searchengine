/* Profoundd - Main JavaScript */

function copyShareLink(btn) {
    var url = window.location.href;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function() {
            showCopied(btn);
        });
    } else {
        // Fallback for older browsers / non-HTTPS
        var tmp = document.createElement('textarea');
        tmp.value = url;
        tmp.style.position = 'fixed';
        tmp.style.opacity = '0';
        document.body.appendChild(tmp);
        tmp.select();
        document.execCommand('copy');
        document.body.removeChild(tmp);
        showCopied(btn);
    }
}
function showCopied(btn) {
    var orig = btn.innerHTML;
    btn.classList.add('copied');
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Link Copied!';
    setTimeout(function() {
        btn.classList.remove('copied');
        btn.innerHTML = orig;
    }, 2000);
}

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

    // --- Credibility Disclaimer Popup ---
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('credibility-help')) {
            e.preventDefault();
            e.stopPropagation();
            // Remove any existing disclaimer
            var existing = document.querySelector('.credibility-overlay');
            if (existing) existing.remove();
            existing = document.querySelector('.credibility-disclaimer');
            if (existing) existing.remove();

            var overlay = document.createElement('div');
            overlay.className = 'credibility-overlay';

            var popup = document.createElement('div');
            popup.className = 'credibility-disclaimer';
            popup.innerHTML = '<strong>Credibility Rating</strong>' +
                'This credibility rating is an opinion and is not to be construed as medical or legal advice.' +
                '<br><button class="credibility-disclaimer-close">OK</button>';

            document.body.appendChild(overlay);
            document.body.appendChild(popup);

            function closeDisclaimer() {
                overlay.remove();
                popup.remove();
            }
            overlay.addEventListener('click', closeDisclaimer);
            popup.querySelector('.credibility-disclaimer-close').addEventListener('click', closeDisclaimer);
        }
    });

    // --- Theme Toggle ---
    var themeIcons = document.querySelectorAll('#themeIcon, #mobileThemeIcon');
    function updateThemeIcons() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        themeIcons.forEach(function(icon) { icon.textContent = isDark ? '🌙' : '☀️'; });
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute('content', isDark ? '#0a0a0f' : '#ffffff');
    }
    updateThemeIcons();
    function handleThemeToggle() {
        var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        if (isDark) {
            document.documentElement.removeAttribute('data-theme');
            localStorage.setItem('theme', 'light');
        } else {
            document.documentElement.setAttribute('data-theme', 'dark');
            localStorage.setItem('theme', 'dark');
        }
        updateThemeIcons();
    }
    document.querySelectorAll('#themeToggle, #mobileThemeToggle').forEach(function(btn) {
        btn.addEventListener('click', handleThemeToggle);
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

/* Admin inline credibility editor */
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.cred-save-btn');
    if (!btn) return;
    var badge = btn.closest('.admin-editable-cred');
    var input = badge.querySelector('.cred-input');
    var url = badge.getAttribute('data-url');
    var val = parseInt(input.value, 10);
    if (!url || isNaN(val) || val < 1 || val > 10) return;

    btn.textContent = '...';
    fetch('/admin/api/update-credibility', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: url, credibility: val})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            btn.innerHTML = '&#10003;';
            btn.classList.add('saved');
            // Update badge color class
            badge.className = badge.className.replace(/credibility-(high|medium|low)/g, '');
            if (val >= 8) badge.classList.add('credibility-high');
            else if (val >= 5) badge.classList.add('credibility-medium');
            else badge.classList.add('credibility-low');
            setTimeout(function() { btn.classList.remove('saved'); }, 2000);
        } else {
            btn.textContent = '!';
            alert(data.error || 'Update failed');
        }
    })
    .catch(function() {
        btn.textContent = '!';
        alert('Network error');
    });
});

/* Admin boost promote/demote (1-10 scale, 5=neutral) */
function updateBoostDisplay(el, val) {
    el.textContent = val;
    el.className = 'boost-value ' + (val > 5 ? 'positive' : (val < 5 ? 'negative' : 'neutral'));
}

function reorderArticleInList(card, direction) {
    /* Move the article card up or down in its parent grid/container */
    var parent = card.parentNode;
    if (!parent) return;

    if (direction === 'promote') {
        var prev = card.previousElementSibling;
        if (prev) {
            card.style.transition = 'transform 0.25s ease';
            card.style.transform = 'translateY(-8px)';
            setTimeout(function() {
                parent.insertBefore(card, prev);
                card.style.transform = '';
                setTimeout(function() { card.style.transition = ''; }, 250);
            }, 150);
        }
    } else {
        var next = card.nextElementSibling;
        if (next) {
            card.style.transition = 'transform 0.25s ease';
            card.style.transform = 'translateY(8px)';
            setTimeout(function() {
                parent.insertBefore(card, next.nextElementSibling);
                card.style.transform = '';
                setTimeout(function() { card.style.transition = ''; }, 250);
            }, 150);
        }
    }
}

document.addEventListener('click', function(e) {
    var btn = e.target.closest('.boost-btn');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();

    var controls = btn.closest('.admin-boost-controls');
    if (!controls) return;
    var url = controls.getAttribute('data-url');
    if (!url) return;

    var direction = btn.classList.contains('boost-up') ? 'promote' : 'demote';
    var valueEl = controls.querySelector('.boost-value');
    var currentBoost = parseInt(controls.getAttribute('data-boost'), 10) || 5;

    // Clamp: don't go below 1 or above 10
    var preview = currentBoost + (direction === 'promote' ? 1 : -1);
    if (preview < 1 || preview > 10) return;

    // Immediate visual feedback
    updateBoostDisplay(valueEl, preview);
    btn.style.opacity = '0.4';

    // Move the article card up or down in the list
    var card = controls.closest('.trending-card, .result-card, article');
    if (card) {
        reorderArticleInList(card, direction);
    }

    // Get the search query from the page if available
    var searchInput = document.querySelector('input[name="q"]');
    var searchQuery = searchInput ? searchInput.value : '';

    fetch('/admin/api/update-boost', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: url, direction: direction, search_query: searchQuery})
    })
    .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
    })
    .then(function(data) {
        btn.style.opacity = '';
        if (data.success) {
            controls.setAttribute('data-boost', data.boost);
            updateBoostDisplay(valueEl, data.boost);
        } else {
            // Revert on failure
            updateBoostDisplay(valueEl, currentBoost);
            alert(data.error || 'Update failed');
        }
    })
    .catch(function(err) {
        btn.style.opacity = '';
        // Revert on error
        updateBoostDisplay(valueEl, currentBoost);
        alert('Boost update failed: ' + err.message);
    });
});

// Initialize boost value colors on page load
document.querySelectorAll('.boost-value').forEach(function(el) {
    updateBoostDisplay(el, parseInt(el.textContent, 10) || 5);
});
