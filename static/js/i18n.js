/*
 * OQB UI language helpers (docs/modules/i18n.md, ADR-017).
 *
 * OQB.t(text, params)  -> text in the UI language. The catalogue is
 *   window.OQB_I18N (served by /i18n/zh.js only when the UI is Chinese),
 *   keyed by the English text; a missing key returns the English text.
 *   `{name}` placeholders are filled from `params` (same syntax as the
 *   Python t()).
 * OQB.setLang(kind, value) -> kind 'ui' (en|zh), 'tax' (en|zh|both) or
 *   'paper' (en|zh|bi); writes the per-device cookie and reloads.
 * OQB.paperLang() -> preferred paper language, or null when unset. The
 *   server turns it into OQB_DEFAULT_VERSION_PRIORITY.
 * OQB.taxName(node) -> display name of an API node that carries
 *   name_en / name_zh (falls back to node.name), in the taxonomy mode.
 * OQB.taxLabel(node) -> node.name with the subject's number prefix
 *   (node.number, "01 Basic Algebra") from /dashboard/api/topics|subtopics.
 *   A name that already starts with that number ("21 Astronomy") is not
 *   prefixed again. Mirrors app/tax_numbers.prefixed. Display only; post
 *   ids or name_en, never the label.
 */
(function () {
    var OQB = window.OQB = window.OQB || {};
    var COOKIES = { ui: 'oqb_lang', tax: 'oqb_tax_lang', paper: 'oqb_paper_lang' };
    var ALLOWED = { ui: ['en', 'zh'], tax: ['en', 'zh', 'both'], paper: ['en', 'zh', 'bi'] };
    // Dashboard keys that would override the new default version order.
    var PAPER_RESET_KEYS = ['oqb_versionPriority', 'oqb_previewLanguage'];

    function readCookie(name) {
        var parts = document.cookie ? document.cookie.split('; ') : [];
        for (var i = 0; i < parts.length; i++) {
            var eq = parts[i].indexOf('=');
            if (parts[i].slice(0, eq) === name) return decodeURIComponent(parts[i].slice(eq + 1));
        }
        return null;
    }

    OQB.uiLang = function () {
        var v = readCookie(COOKIES.ui);
        return ALLOWED.ui.indexOf(v) >= 0 ? v : 'en';
    };

    OQB.taxLang = function () {
        var v = readCookie(COOKIES.tax);
        return ALLOWED.tax.indexOf(v) >= 0 ? v : 'en';
    };

    OQB.paperLang = function () {
        var v = readCookie(COOKIES.paper);
        return ALLOWED.paper.indexOf(v) >= 0 ? v : null;
    };

    OQB.t = function (text, params) {
        var out = text == null ? '' : String(text);
        var cat = window.OQB_I18N;
        if (cat && OQB.uiLang() === 'zh' && Object.prototype.hasOwnProperty.call(cat, out) && cat[out]) {
            out = cat[out];
        }
        if (params) {
            out = out.replace(/\{(\w+)\}/g, function (m, key) {
                return Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : m;
            });
        }
        return out;
    };

    OQB.bilingual = function (name, nameZh, mode) {
        name = name || '';
        var zh = (nameZh || '').trim();
        mode = mode || OQB.taxLang();
        if (mode === 'zh') return zh || name;
        if (mode === 'both' && zh && zh !== name) return name + ' ' + zh;
        return name;
    };

    OQB.taxName = function (node) {
        if (!node) return '';
        if (node.name_en === undefined) return node.name || '';
        return OQB.bilingual(node.name_en, node.name_zh);
    };

    // Same rule as app/tax_numbers._number_key: "01" and "1" match, "21st" does not.
    function taxNumberKey(token) {
        token = String(token == null ? '' : token).replace(/\.+$/, '');
        if (!token) return null;
        var parts = token.split('.');
        var nums = [];
        for (var i = 0; i < parts.length; i++) {
            if (!/^\d+$/.test(parts[i])) return null;
            nums.push(String(parseInt(parts[i], 10)));
        }
        return nums.join('.');
    }

    OQB.taxLabel = function (node) {
        if (!node) return '';
        var name = node.name || '';
        var number = node.number;
        if (!number) return name;
        var first = name.split(/\s+/)[0] || '';
        if (first === String(number) || (taxNumberKey(first) && taxNumberKey(first) === taxNumberKey(number))) {
            return name;
        }
        return number + ' ' + name;
    };

    // Lower-cased "English 中文" haystack for client-side search boxes.
    OQB.taxSearchKey = function (node) {
        if (!node) return '';
        return [node.name, node.name_en, node.name_zh].filter(Boolean).join(' ').toLowerCase();
    };

    OQB.setLang = function (kind, value) {
        var name = COOKIES[kind];
        if (!name || ALLOWED[kind].indexOf(value) < 0) return;
        document.cookie = name + '=' + encodeURIComponent(value) +
            '; path=/; max-age=31536000; SameSite=Lax';
        if (kind === 'paper') {
            try {
                PAPER_RESET_KEYS.forEach(function (k) { localStorage.removeItem(k); });
            } catch (e) { /* storage blocked: the cookie still sets the default */ }
        }
        window.location.reload();
    };
})();
