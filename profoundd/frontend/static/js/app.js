/* Profoundd - Main JavaScript */

function copyShareLink(btn) {
    var url = window.location.href;
    // Build share text: article title + "Found on Profoundd" + link
    var title = document.querySelector('h1');
    var shareText = (title ? title.textContent.trim() + '\n' : '') +
                    'Found on Profoundd\n' + url;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(shareText).then(function() {
            showCopied(btn);
        });
    } else {
        // Fallback for older browsers / non-HTTPS
        var tmp = document.createElement('textarea');
        tmp.value = shareText;
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
            // Update ALL visible badges for this source on the page
            var srcName = data.source_name;
            if (srcName) {
                document.querySelectorAll('.admin-editable-cred').forEach(function(b) {
                    var bInput = b.querySelector('.cred-input');
                    // Find the source name near this badge
                    var card = b.closest('article, .article-detail, .result-card, .trending-card');
                    if (!card) return;
                    var srcEl = card.querySelector('.result-source, .source');
                    if (!srcEl) return;
                    // Check if this card's source matches
                    var cardSrc = srcEl.textContent.trim().split('\n')[0].trim();
                    if (cardSrc === srcName || cardSrc.indexOf(srcName) === 0) {
                        bInput.value = val;
                        b.className = b.className.replace(/credibility-(high|medium|low)/g, '');
                        if (val >= 8) b.classList.add('credibility-high');
                        else if (val >= 5) b.classList.add('credibility-medium');
                        else b.classList.add('credibility-low');
                    }
                });
            } else {
                // Fallback: just update this one badge
                badge.className = badge.className.replace(/credibility-(high|medium|low)/g, '');
                if (val >= 8) badge.classList.add('credibility-high');
                else if (val >= 5) badge.classList.add('credibility-medium');
                else badge.classList.add('credibility-low');
            }
            var msg = data.articles_updated
                ? data.articles_updated + ' articles from ' + srcName + ' updated'
                : 'Updated';
            btn.title = msg;
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

/* NewsRoom Bob - "Bob, Write This" button handler with category picker */
function showBobCategoryPicker(btn) {
    // Remove any existing picker
    var old = document.getElementById('bob-cat-picker');
    if (old) old.remove();

    var cats = window.__CATEGORIES || {};
    var origCat = btn.getAttribute('data-category') || 'news';

    var overlay = document.createElement('div');
    overlay.id = 'bob-cat-picker';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';

    var modal = document.createElement('div');
    modal.style.cssText = 'background:var(--bg-card,#fff);border-radius:12px;padding:24px;max-width:420px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.3);';

    var title = document.createElement('h3');
    title.style.cssText = 'margin:0 0 6px;font-size:1.1rem;';
    title.textContent = 'Select Categories for Bob\'s Story';

    var subtitle = document.createElement('p');
    subtitle.style.cssText = 'margin:0 0 16px;font-size:0.85rem;color:var(--text-secondary,#666);';
    subtitle.textContent = 'Pick one or more categories where this story should appear.';

    modal.appendChild(title);
    modal.appendChild(subtitle);

    var checkboxes = [];
    Object.keys(cats).forEach(function(key) {
        var label = document.createElement('label');
        label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:6px;cursor:pointer;margin-bottom:4px;transition:background 0.15s;';
        label.onmouseenter = function() { label.style.background = 'var(--bg-secondary,#f1f5f9)'; };
        label.onmouseleave = function() { label.style.background = 'transparent'; };

        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = key;
        cb.checked = (key === origCat);
        cb.style.cssText = 'width:18px;height:18px;cursor:pointer;';

        var txt = document.createElement('span');
        txt.style.cssText = 'font-size:0.9rem;';
        txt.textContent = cats[key];

        label.appendChild(cb);
        label.appendChild(txt);
        modal.appendChild(label);
        checkboxes.push(cb);
    });

    var btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:10px;margin-top:18px;justify-content:flex-end;';

    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.cssText = 'padding:8px 18px;border-radius:6px;border:1px solid var(--border,#ddd);background:transparent;cursor:pointer;font-size:0.9rem;color:var(--text,#333);';
    cancelBtn.onclick = function() { overlay.remove(); };

    var goBtn = document.createElement('button');
    goBtn.type = 'button';
    goBtn.textContent = 'Bob, Write This';
    goBtn.style.cssText = 'padding:8px 18px;border-radius:6px;border:none;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;cursor:pointer;font-weight:600;font-size:0.9rem;';
    goBtn.onclick = function() {
        var selected = [];
        checkboxes.forEach(function(cb) { if (cb.checked) selected.push(cb.value); });
        if (selected.length === 0) {
            alert('Please select at least one category.');
            return;
        }
        overlay.remove();
        triggerBobWrite(btn, selected);
    };

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(goBtn);
    modal.appendChild(btnRow);

    overlay.appendChild(modal);
    overlay.onclick = function(ev) { if (ev.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
}

function triggerBobWrite(btn, categories) {
    if (btn.disabled) return;
    btn.disabled = true;
    var origText = btn.textContent;
    btn.textContent = 'Bob is writing...';
    btn.classList.add('bob-working');

    var data = {
        url: btn.getAttribute('data-url'),
        title: btn.getAttribute('data-title'),
        source_name: btn.getAttribute('data-source'),
        categories: categories,
        summary: btn.getAttribute('data-summary')
    };

    fetch('/admin/bob/write', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    })
    .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.error || 'Failed'); });
        return r.json();
    })
    .then(function(result) {
        btn.classList.remove('bob-working');
        btn.classList.add('bob-done');
        if (result.already_exists) {
            btn.innerHTML = '<a href="/newsroom/' + result.slug + '" style="color:inherit;text-decoration:none">Already Written &rarr;</a>';
        } else {
            btn.innerHTML = '<a href="/newsroom/' + result.slug + '" style="color:inherit;text-decoration:none">Read Bob\'s Story &rarr;</a>';
        }
    })
    .catch(function(err) {
        btn.classList.remove('bob-working');
        btn.disabled = false;
        btn.textContent = origText;
        alert('Bob failed: ' + err.message);
    });
}

document.addEventListener('click', function(e) {
    var btn = e.target.closest('.bob-write-btn');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    if (btn.disabled) return;
    showBobCategoryPicker(btn);
});

/* Admin: Add Category to Article */
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.admin-add-cat-btn');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();

    // Toggle dropdown
    var existing = btn.parentElement.querySelector('.admin-cat-dropdown');
    if (existing) { existing.remove(); return; }

    // Close any other open dropdowns
    document.querySelectorAll('.admin-cat-dropdown').forEach(function(d) { d.remove(); });

    var cats = window.__CATEGORIES || {};
    var articleUrl = btn.getAttribute('data-url');

    var dropdown = document.createElement('div');
    dropdown.className = 'admin-cat-dropdown';
    dropdown.style.cssText = 'position:absolute;top:100%;left:0;z-index:100;background:var(--bg-card,#fff);border:1px solid var(--border,#ddd);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.15);min-width:180px;max-height:300px;overflow-y:auto;padding:4px;';

    Object.keys(cats).forEach(function(key) {
        var item = document.createElement('button');
        item.type = 'button';
        item.textContent = cats[key];
        item.style.cssText = 'display:block;width:100%;text-align:left;padding:8px 12px;border:none;background:transparent;cursor:pointer;font-size:0.85rem;border-radius:4px;color:var(--text,#333);';
        item.onmouseenter = function() { item.style.background = 'var(--bg-secondary,#f1f5f9)'; };
        item.onmouseleave = function() { item.style.background = 'transparent'; };
        item.onclick = function() {
            item.textContent = 'Adding...';
            fetch('/admin/api/add-category', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: articleUrl, category: key})
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) {
                    item.textContent = data.label + ' ✓';
                    item.style.color = '#22c55e';
                    item.style.fontWeight = '600';
                    setTimeout(function() { dropdown.remove(); }, 800);
                } else {
                    item.textContent = data.error || 'Failed';
                    item.style.color = '#ef4444';
                }
            })
            .catch(function() {
                item.textContent = 'Error';
                item.style.color = '#ef4444';
            });
        };
        dropdown.appendChild(item);
    });

    btn.parentElement.style.position = 'relative';
    btn.parentElement.appendChild(dropdown);

    // Close on outside click
    setTimeout(function() {
        document.addEventListener('click', function closeDropdown(ev) {
            if (!dropdown.contains(ev.target) && ev.target !== btn) {
                dropdown.remove();
                document.removeEventListener('click', closeDropdown);
            }
        });
    }, 10);
});

/* NewsRoom Notes — Admin add/edit/delete/enhance */
function showNoteEditor(wrapper, existingText) {
    // Remove any existing editor in this wrapper
    var old = wrapper.querySelector('.note-editor');
    if (old) old.remove();

    var url = wrapper.getAttribute('data-url');
    var title = wrapper.getAttribute('data-title') || '';
    var summary = wrapper.getAttribute('data-summary') || '';

    var editor = document.createElement('div');
    editor.className = 'note-editor';
    editor.innerHTML =
        '<textarea class="note-textarea" placeholder="Write your editorial note...">' + (existingText || '') + '</textarea>' +
        '<div class="note-editor-actions">' +
        '<button type="button" class="note-save-btn">Save Note</button>' +
        '<button type="button" class="note-bob-btn" title="Have NewsRoom Bob improve this note">Bob, Improve This</button>' +
        '<button type="button" class="note-cancel-btn">Cancel</button>' +
        '</div>';
    wrapper.appendChild(editor);

    var textarea = editor.querySelector('.note-textarea');
    textarea.focus();

    // Save
    editor.querySelector('.note-save-btn').addEventListener('click', function() {
        var text = textarea.value.trim();
        if (!text) return alert('Note cannot be empty.');
        this.disabled = true;
        this.textContent = 'Saving...';
        fetch('/admin/api/newsroom-note', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({url: url, note_text: text, article_title: title})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) throw new Error(data.error);
            // Update the display
            var noteDiv = wrapper.querySelector('.newsroom-note');
            if (!noteDiv) {
                noteDiv = document.createElement('div');
                noteDiv.className = 'newsroom-note';
                wrapper.insertBefore(noteDiv, wrapper.firstChild);
            }
            noteDiv.innerHTML = '<span class="newsroom-note-label">NewsRoom Note</span><span class="newsroom-note-text">' + data.note_text + '</span>';
            // Update controls to show edit/delete
            var controls = wrapper.querySelector('.newsroom-note-controls');
            if (controls) {
                controls.innerHTML = '<button type="button" class="note-edit-btn">Edit Note</button><button type="button" class="note-delete-btn" title="Delete note">&#10005;</button>';
            }
            editor.remove();
        })
        .catch(function(err) { alert('Save failed: ' + err.message); });
    });

    // Bob enhance
    editor.querySelector('.note-bob-btn').addEventListener('click', function() {
        var text = textarea.value.trim();
        if (!text) return alert('Write something first, then Bob can improve it.');
        this.disabled = true;
        this.textContent = 'Bob is writing...';
        var bobBtn = this;
        fetch('/admin/api/newsroom-note/enhance', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({note_text: text, article_title: title, article_summary: summary})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) throw new Error(data.error);
            textarea.value = data.enhanced_text;
            bobBtn.disabled = false;
            bobBtn.textContent = 'Bob, Improve This';
        })
        .catch(function(err) {
            bobBtn.disabled = false;
            bobBtn.textContent = 'Bob, Improve This';
            alert('Bob failed: ' + err.message);
        });
    });

    // Cancel
    editor.querySelector('.note-cancel-btn').addEventListener('click', function() {
        editor.remove();
    });
}

// Add Note button
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.note-add-btn');
    if (!btn) return;
    var wrapper = btn.closest('.newsroom-note-wrapper');
    if (wrapper) showNoteEditor(wrapper, '');
});

// Edit Note button
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.note-edit-btn');
    if (!btn) return;
    var wrapper = btn.closest('.newsroom-note-wrapper');
    if (!wrapper) return;
    var noteText = wrapper.querySelector('.newsroom-note-text');
    showNoteEditor(wrapper, noteText ? noteText.textContent.trim() : '');
});

// Delete Note button
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.note-delete-btn');
    if (!btn) return;
    if (!confirm('Delete this NewsRoom Note?')) return;
    var wrapper = btn.closest('.newsroom-note-wrapper');
    if (!wrapper) return;
    var url = wrapper.getAttribute('data-url');
    fetch('/admin/api/newsroom-note', {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: url})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) throw new Error(data.error);
        // Remove note display
        var noteDiv = wrapper.querySelector('.newsroom-note');
        if (noteDiv) noteDiv.remove();
        // Update controls to show add button
        var controls = wrapper.querySelector('.newsroom-note-controls');
        if (controls) {
            controls.innerHTML = '<button type="button" class="note-add-btn">+ Add Note</button>';
        }
    })
    .catch(function(err) { alert('Delete failed: ' + err.message); });
});

/* --- Source Notes (credibility notes on content sources) --- */

// Toggle source note popup
document.addEventListener('click', function(e) {
    var badge = e.target.closest('.source-note-badge');
    if (!badge) return;
    var wrapper = badge.closest('.source-note-wrapper');
    var popup = wrapper.querySelector('.source-note-popup');
    if (popup) popup.style.display = popup.style.display === 'none' ? 'block' : 'none';
});

// Close popup
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.source-note-close');
    if (!btn) return;
    btn.closest('.source-note-popup').style.display = 'none';
});

// Add source note (admin)
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.srcnote-add-btn');
    if (!btn) return;
    var wrapper = btn.closest('.source-note-wrapper');
    if (wrapper) showSourceNoteEditor(wrapper, '', 'neutral');
});

// Edit source note (admin)
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.srcnote-edit-btn');
    if (!btn) return;
    var wrapper = btn.closest('.source-note-wrapper');
    if (!wrapper) return;
    var body = wrapper.querySelector('.source-note-body');
    var stanceLabel = wrapper.querySelector('.source-note-stance-label');
    var stance = 'neutral';
    if (stanceLabel) {
        if (stanceLabel.classList.contains('source-stance-trustworthy')) stance = 'trustworthy';
        else if (stanceLabel.classList.contains('source-stance-caution')) stance = 'caution';
    }
    var popup = wrapper.querySelector('.source-note-popup');
    if (popup) popup.style.display = 'none';
    showSourceNoteEditor(wrapper, body ? body.textContent.trim() : '', stance);
});

// Delete source note (admin)
document.addEventListener('click', function(e) {
    var btn = e.target.closest('.srcnote-delete-btn');
    if (!btn) return;
    if (!confirm('Delete this source note?')) return;
    var wrapper = btn.closest('.source-note-wrapper');
    if (!wrapper) return;
    var srcName = wrapper.getAttribute('data-source');
    fetch('/admin/api/source-note', {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({source_name: srcName})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) throw new Error(data.error);
        var popup = wrapper.querySelector('.source-note-popup');
        var badge = wrapper.querySelector('.source-note-badge');
        if (popup) popup.remove();
        if (badge) badge.remove();
        // Show add button for admin
        wrapper.innerHTML = '<button type="button" class="srcnote-add-btn" title="Add source credibility note">&#9432;</button>';
        wrapper.setAttribute('data-source', srcName);
    })
    .catch(function(err) { alert('Delete failed: ' + err.message); });
});

function showSourceNoteEditor(wrapper, existingText, stance) {
    var old = wrapper.querySelector('.srcnote-editor');
    if (old) old.remove();

    var srcName = wrapper.getAttribute('data-source');
    var editor = document.createElement('div');
    editor.className = 'srcnote-editor';
    editor.innerHTML =
        '<div class="srcnote-editor-title">Source Note: ' + srcName + '</div>' +
        '<textarea class="srcnote-textarea" placeholder="Why is this source trustworthy or not? Include evidence...">' + (existingText || '') + '</textarea>' +
        '<div class="srcnote-stance-row">' +
        '<label>Stance:</label>' +
        '<select class="srcnote-stance">' +
        '<option value="neutral"' + (stance === 'neutral' ? ' selected' : '') + '>Neutral</option>' +
        '<option value="trustworthy"' + (stance === 'trustworthy' ? ' selected' : '') + '>Trustworthy</option>' +
        '<option value="caution"' + (stance === 'caution' ? ' selected' : '') + '>Use Caution</option>' +
        '</select>' +
        '</div>' +
        '<div class="srcnote-guidance-row">' +
        '<label>Guide the AI <small>(optional — tell the AI what to focus on for this source)</small></label>' +
        '<textarea class="srcnote-guidance" rows="2" placeholder="e.g. Focus on their track record with fact-checking. Look at their retraction policy. Check if they were involved in the 2022 defamation lawsuit..."></textarea>' +
        '</div>' +
        '<div class="srcnote-editor-actions">' +
        '<button type="button" class="srcnote-save">Save</button>' +
        '<button type="button" class="srcnote-research">AI Research This Source</button>' +
        '<button type="button" class="srcnote-research-claim">AI Find Evidence</button>' +
        '<button type="button" class="srcnote-cancel">Cancel</button>' +
        '</div>' +
        '<div class="srcnote-guidelines-hint">Site-wide AI guidelines are set in <a href="/admin/ai-settings" target="_blank">AI Settings</a></div>';

    // Position near the wrapper
    wrapper.appendChild(editor);
    editor.querySelector('.srcnote-textarea').focus();

    // Save
    editor.querySelector('.srcnote-save').addEventListener('click', function() {
        var text = editor.querySelector('.srcnote-textarea').value.trim();
        var st = editor.querySelector('.srcnote-stance').value;
        if (!text) return alert('Note cannot be empty.');
        this.disabled = true;
        this.textContent = 'Saving...';
        fetch('/admin/api/source-note', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source_name: srcName, note_text: text, stance: st})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) throw new Error(data.error);
            location.reload();
        })
        .catch(function(err) { alert('Save failed: ' + err.message); });
    });

    // AI Research (general)
    editor.querySelector('.srcnote-research').addEventListener('click', function() {
        this.disabled = true;
        this.textContent = 'Researching...';
        var resBtn = this;
        var guidance = editor.querySelector('.srcnote-guidance').value.trim();
        fetch('/admin/api/source-note/research', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source_name: srcName, guidance: guidance})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) throw new Error(data.error);
            editor.querySelector('.srcnote-textarea').value = data.research_text;
            resBtn.disabled = false;
            resBtn.textContent = 'AI Research This Source';
        })
        .catch(function(err) {
            resBtn.disabled = false;
            resBtn.textContent = 'AI Research This Source';
            alert('Research failed: ' + err.message);
        });
    });

    // AI Find Evidence (with admin's claim)
    editor.querySelector('.srcnote-research-claim').addEventListener('click', function() {
        var claim = editor.querySelector('.srcnote-textarea').value.trim();
        if (!claim) return alert('Write your claim first (e.g. "This source has been caught fabricating quotes"), then click this button to find evidence.');
        this.disabled = true;
        this.textContent = 'Finding evidence...';
        var evidBtn = this;
        var guidance = editor.querySelector('.srcnote-guidance').value.trim();
        fetch('/admin/api/source-note/research', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source_name: srcName, claim: claim, guidance: guidance})
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) throw new Error(data.error);
            editor.querySelector('.srcnote-textarea').value = data.research_text;
            evidBtn.disabled = false;
            evidBtn.textContent = 'AI Find Evidence';
        })
        .catch(function(err) {
            evidBtn.disabled = false;
            evidBtn.textContent = 'AI Find Evidence';
            alert('Research failed: ' + err.message);
        });
    });

    // Cancel
    editor.querySelector('.srcnote-cancel').addEventListener('click', function() {
        editor.remove();
    });
}

/* =========================================
   Cookie Consent Banner
   ========================================= */
(function() {
    var banner = document.getElementById('cookieConsent');
    var acceptBtn = document.getElementById('cookieAccept');
    var declineBtn = document.getElementById('cookieDecline');
    if (!banner) return;

    // Show banner if no consent decision has been made
    var consent = getCookie('cookie_consent');
    if (!consent) {
        banner.style.display = 'flex';
    }

    if (acceptBtn) {
        acceptBtn.addEventListener('click', function() {
            setCookie('cookie_consent', 'accepted', 365);
            banner.style.display = 'none';
            // Start tracking now that consent is given
            window.__pfStartTracking && window.__pfStartTracking();
        });
    }

    if (declineBtn) {
        declineBtn.addEventListener('click', function() {
            setCookie('cookie_consent', 'declined', 365);
            banner.style.display = 'none';
            // Remove the visitor ID cookie if it exists
            setCookie('profoundd_vid', '', -1);
        });
    }

    function getCookie(name) {
        var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return match ? match[2] : null;
    }

    function setCookie(name, value, days) {
        var d = new Date();
        d.setTime(d.getTime() + days * 24 * 60 * 60 * 1000);
        var secure = location.protocol === 'https:' ? ';Secure' : '';
        document.cookie = name + '=' + value + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax' + secure;
    }
})();

/* =========================================
   Enhanced Analytics Tracking
   ========================================= */
(function() {
    function startTracking() {
        // Don't track admin pages
        if (location.pathname.startsWith('/admin')) return;

        // Check cookie consent
        var match = document.cookie.match(/(^| )cookie_consent=([^;]+)/);
        if (!match || match[2] !== 'accepted') return;

        // Session ID (tab-scoped, dies on tab close)
        var sid = sessionStorage.getItem('pf_sid');
        if (!sid) {
            sid = Math.random().toString(36).slice(2) + Date.now().toString(36);
            sessionStorage.setItem('pf_sid', sid);
        }

        // Visitor ID (persistent across sessions)
        var vid = localStorage.getItem('pf_vid');
        if (!vid) {
            vid = Math.random().toString(36).slice(2) + Date.now().toString(36);
            localStorage.setItem('pf_vid', vid);
        }

        var pvId = null;
        var loadTime = Date.now();

        // Send page view
        fetch('/api/analytics/track', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                path: location.pathname,
                referrer: document.referrer || null,
                sessionId: sid,
                visitorId: vid,
                screenWidth: screen.width || null,
                screenHeight: screen.height || null,
                language: navigator.language || null
            })
        }).then(function(r) { return r.json(); })
          .then(function(data) { if (data.id) pvId = data.id; })
          .catch(function() {});

        // Send duration on page unload via sendBeacon
        function sendDuration() {
            if (!pvId) return;
            var dur = Math.round((Date.now() - loadTime) / 1000);
            if (dur < 1 || dur > 3600) return;
            var blob = new Blob([JSON.stringify({id: pvId, duration: dur})], {type: 'application/json'});
            navigator.sendBeacon('/api/analytics/duration', blob);
        }
        window.addEventListener('pagehide', sendDuration);
    }

    // Expose for cookie consent handler to call after accept
    window.__pfStartTracking = startTracking;

    // Start immediately if consent already given
    startTracking();
})();
