/* CSRF token for every same-origin POST / PUT / PATCH / DELETE
 * (docs/frontend/conventions.md, app/csrf.py).
 *
 * Reads <meta name="csrf-token"> and adds it to:
 *   - window.fetch and XMLHttpRequest   -> X-CSRF-Token header
 *   - htmx requests (htmx:configRequest) -> X-CSRF-Token header
 *   - <form method="post"> submits and form.submit() -> hidden csrf_token
 * Load it before htmx and every page script. Exposes OQB.csrfToken() and
 * OQB.csrfInput() for code that builds requests by hand.
 */
(function () {
    'use strict';

    var HEADER = 'X-CSRF-Token';
    var FIELD = 'csrf_token';
    var UNSAFE = /^(POST|PUT|PATCH|DELETE)$/i;

    function token() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? (meta.getAttribute('content') || '') : '';
    }

    function sameOrigin(url) {
        try {
            return new URL(String(url), window.location.href).origin === window.location.origin;
        } catch (e) {
            return false;
        }
    }

    var OQB = window.OQB = window.OQB || {};
    OQB.csrfToken = token;
    OQB.csrfInput = function () {
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = FIELD;
        input.value = token();
        return input;
    };

    if (window.fetch) {
        var nativeFetch = window.fetch;
        window.fetch = function (input, init) {
            try {
                var req = (typeof Request !== 'undefined' && input instanceof Request) ? input : null;
                var method = (init && init.method) || (req && req.method) || 'GET';
                var url = req ? req.url : input;
                if (UNSAFE.test(method) && sameOrigin(url) && token()) {
                    var headers = new Headers((init && init.headers) || (req ? req.headers : undefined));
                    if (!headers.has(HEADER)) {
                        headers.set(HEADER, token());
                        init = Object.assign({}, init || {}, { headers: headers });
                    }
                }
            } catch (e) { /* never block the request */ }
            return nativeFetch.call(this, input, init);
        };
    }

    var xhr = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
    if (xhr) {
        var nativeOpen = xhr.open;
        var nativeSend = xhr.send;
        var nativeSetHeader = xhr.setRequestHeader;
        xhr.open = function (method, url) {
            this._oqbCsrf = UNSAFE.test(String(method || '')) && sameOrigin(url);
            this._oqbCsrfSet = false;
            return nativeOpen.apply(this, arguments);
        };
        xhr.setRequestHeader = function (name) {
            if (String(name).toLowerCase() === HEADER.toLowerCase()) this._oqbCsrfSet = true;
            return nativeSetHeader.apply(this, arguments);
        };
        xhr.send = function () {
            if (this._oqbCsrf && !this._oqbCsrfSet && token()) {
                nativeSetHeader.call(this, HEADER, token());
            }
            return nativeSend.apply(this, arguments);
        };
    }

    document.addEventListener('htmx:configRequest', function (evt) {
        var detail = evt.detail;
        if (detail && detail.headers && UNSAFE.test(String(detail.verb || '')) && token()) {
            detail.headers[HEADER] = token();
        }
    });

    function addField(form) {
        if (!/^post$/i.test(form.getAttribute('method') || '')) return;
        if (!sameOrigin(form.getAttribute('action') || window.location.href)) return;
        var existing = form.querySelector('input[name="' + FIELD + '"]');
        if (!existing) {
            form.appendChild(OQB.csrfInput());
        } else if (!existing.value) {
            existing.value = token();
        }
    }

    document.addEventListener('submit', function (evt) {
        if (evt.target && evt.target.tagName === 'FORM') addField(evt.target);
    }, true);

    var nativeSubmit = HTMLFormElement.prototype.submit;
    HTMLFormElement.prototype.submit = function () {
        addField(this);
        return nativeSubmit.apply(this, arguments);
    };
})();
