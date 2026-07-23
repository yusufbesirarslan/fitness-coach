(function () {
    'use strict';

    var MOUNT_SELECTOR = '[data-weekly-program-mount]';
    var COPY_SELECTOR = '[data-weekly-program-copy]';
    var ENDPOINT = '/api/training/weekly-program';
    var STATES = {
        idle: 'idle',
        loading: 'loading',
        populated: 'populated',
        insufficient: 'insufficient_data',
        missing: 'missing_baseline',
        error: 'error',
        malformed: 'malformed'
    };
    var FOCUSES = ['insufficient_data', 'build_consistency', 'deload',
        'maintenance', 'overload', 'steady'];
    var VOLUME_ACTIONS = ['increase', 'hold', 'decrease'];
    var INTENSITY_ACTIONS = ['progress', 'hold', 'deload'];
    var REASONS = ['insufficient_history', 'inconsistent_training', 'deload_due',
        'plateau_detected', 'progressing', 'steady_state', 'volume_trend_down',
        'strength_trend_down'];
    function makeElement(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function copyText(copy, key) {
        return typeof copy[key] === 'string' ? copy[key] : '';
    }

    function setState(mount, state) {
        mount.dataset.weeklyProgramState = state;
    }

    function heading(copy) {
        return makeElement('h2', 'sec-label weekly-program-heading',
            copyText(copy, 'training.weekly_program'));
    }

    function isCurrentRequest(context, generation) {
        return context.mount.isConnected !== false
            && generation === context.requestGeneration;
    }

    function replaceContent(context, content, focusAfterRetry) {
        var title = heading(context.copy);
        context.mount.replaceChildren(title, content);
        if (focusAfterRetry) {
            title.setAttribute('tabindex', '-1');
            title.focus();
        }
    }

    function renderLoading(context) {
        setState(context.mount, STATES.loading);
        var status = makeElement('div', 'weekly-program-loading');
        status.setAttribute('role', 'status');
        status.append(
            makeElement('p', 'weekly-program-message',
                copyText(context.copy, 'weekly_program.loading')),
            makeElement('div', 'skeleton skeleton-text weekly-program-skeleton'),
            makeElement('div', 'skeleton skeleton-text weekly-program-skeleton')
        );
        context.mount.replaceChildren(heading(context.copy), status);
    }

    function renderFailure(context, malformed, focusAfterRetry) {
        setState(context.mount, malformed ? STATES.malformed : STATES.error);
        var alert = makeElement('div', 'weekly-program-alert');
        alert.setAttribute('role', 'alert');
        alert.append(makeElement(
            'p', 'weekly-program-message',
            copyText(context.copy, malformed
                ? 'weekly_program.state.malformed_response'
                : 'weekly_program.state.error')
        ));
        var retry = makeElement(
            'button', 'btn-ghost weekly-program-retry',
            copyText(context.copy, 'weekly_program.state.retry')
        );
        retry.setAttribute('type', 'button');
        retry.addEventListener('click', function () {
            loadRecommendation(context, true);
        });
        alert.append(retry);
        context.mount.replaceChildren(heading(context.copy), alert);
        if (focusAfterRetry) retry.focus();
    }

    function appendFocus(container, copy, payload) {
        var block = makeElement('div', 'weekly-program-focus');
        block.append(
            makeElement('h3', 'weekly-program-focus-title',
                copyText(copy, 'weekly_program.focus.' + payload.week_focus + '.title')),
            makeElement('p', 'weekly-program-focus-description',
                copyText(copy,
                    'weekly_program.focus.' + payload.week_focus + '.description'))
        );
        container.append(block);
    }

    function appendActions(container, copy, payload) {
        var actions = makeElement('div', 'weekly-program-actions');
        actions.append(
            makeElement('p', 'weekly-program-action',
                copyText(copy, 'weekly_program.volume_action.' + payload.volume_action)),
            makeElement('p', 'weekly-program-action',
                copyText(copy,
                    'weekly_program.intensity_action.' + payload.intensity_action))
        );
        container.append(actions);
    }

    function formatVolume(value, locale, copy) {
        var tag = locale === 'en' ? 'en-US' : 'tr-TR';
        return new Intl.NumberFormat(tag, {
            maximumFractionDigits: 2
        }).format(value) + ' ' + copyText(copy, 'weekly_program.metric.kg');
    }

    function appendMetrics(container, context, payload) {
        var metrics = makeElement('div', 'weekly-program-metrics');
        [
            ['observed', payload.baseline_weekly_volume],
            ['target', payload.target_weekly_volume]
        ].forEach(function (entry) {
            var metric = makeElement('div', 'weekly-program-metric');
            metric.append(
                makeElement('span', 'weekly-program-metric-label',
                    copyText(context.copy, 'weekly_program.metric.' + entry[0])),
                makeElement('strong', 'weekly-program-metric-value',
                    formatVolume(entry[1], context.locale, context.copy))
            );
            metrics.append(metric);
        });
        container.append(metrics);
    }

    function appendReasons(container, copy, payload) {
        var list = makeElement('ul', 'weekly-program-reasons');
        payload.reason_codes.forEach(function (reason) {
            list.append(makeElement('li', 'weekly-program-reason',
                copyText(copy, 'weekly_program.reason.' + reason)));
        });
        container.append(list);
    }

    function renderRecommendation(context, payload, focusAfterRetry) {
        var content = makeElement('div', 'weekly-program-content');
        if (!payload.has_data) {
            setState(context.mount, STATES.insufficient);
            content.append(makeElement(
                'p', 'weekly-program-message',
                copyText(context.copy, 'weekly_program.state.insufficient_data')
            ));
            replaceContent(context, content, focusAfterRetry);
            return;
        }

        appendFocus(content, context.copy, payload);
        appendActions(content, context.copy, payload);
        if (payload.baseline_weekly_volume === null) {
            setState(context.mount, STATES.missing);
            content.append(makeElement(
                'p', 'weekly-program-message',
                copyText(context.copy, 'weekly_program.state.missing_baseline')
            ));
        } else {
            setState(context.mount, STATES.populated);
            appendMetrics(content, context, payload);
            appendReasons(content, context.copy, payload);
        }
        replaceContent(context, content, focusAfterRetry);
    }

    function isPlainObject(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
        var prototype = Object.getPrototypeOf(value);
        return prototype === Object.prototype || prototype === null;
    }

    function isStringArray(value) {
        return Array.isArray(value) && value.every(function (item) {
            return typeof item === 'string';
        });
    }

    function isAllowed(value, allowed) {
        return typeof value === 'string' && allowed.indexOf(value) !== -1;
    }

    function isPositiveFinite(value) {
        return typeof value === 'number' && Number.isFinite(value) && value > 0;
    }

    function isRealIsoDate(value) {
        if (typeof value !== 'string'
                || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
        var parsed = new Date(value + 'T00:00:00Z');
        return !Number.isNaN(parsed.getTime())
            && parsed.toISOString().slice(0, 10) === value;
    }

    function isValidPayload(payload) {
        if (!isPlainObject(payload)
                || !Number.isInteger(payload.weeks) || payload.weeks < 0
                || typeof payload.has_data !== 'boolean'
                || !isAllowed(payload.week_focus, FOCUSES)
                || !isAllowed(payload.volume_action, VOLUME_ACTIONS)
                || !isAllowed(payload.intensity_action, INTENSITY_ACTIONS)
                || typeof payload.volume_delta_pct !== 'number'
                || !Number.isFinite(payload.volume_delta_pct)
                || typeof payload.overload_ready !== 'boolean'
                || typeof payload.maintenance_recommended !== 'boolean'
                || !isStringArray(payload.reason_codes)
                || !isStringArray(payload.explanation_keys)
                || !isStringArray(payload.unsupported)
                || !payload.reason_codes.every(function (reason) {
                    return REASONS.indexOf(reason) !== -1;
                })) return false;

        var expectedKeys = ['weekly_program.focus.' + payload.week_focus];
        payload.reason_codes.forEach(function (reason) {
            expectedKeys.push('weekly_program.reason.' + reason);
        });
        if (payload.explanation_keys.length !== expectedKeys.length
                || !payload.explanation_keys.every(function (key, index) {
                    return key === expectedKeys[index];
                })) return false;

        var bothNull = payload.baseline_weekly_volume === null
            && payload.target_weekly_volume === null;
        var bothPositive = isPositiveFinite(payload.baseline_weekly_volume)
            && isPositiveFinite(payload.target_weekly_volume);
        if (!bothNull && !bothPositive) return false;
        if (bothNull && payload.baseline_week_start !== null) return false;
        if (bothPositive && !isRealIsoDate(payload.baseline_week_start)) return false;
        if (!payload.has_data && !bothNull) return false;
        return true;
    }

    function loadRecommendation(context, focusAfterRetry) {
        if (context.activeRequest) return false;
        context.activeRequest = true;
        context.requestGeneration += 1;
        var generation = context.requestGeneration;
        renderLoading(context);

        fetch(ENDPOINT, {
            method: 'GET',
            headers: {'Accept': 'application/json'}
        }).then(function (response) {
            if (!isCurrentRequest(context, generation)) return null;
            if (response.redirected || !response.ok) throw new Error('request failed');
            return response.json().catch(function () {
                throw new Error('invalid json');
            });
        }).then(function (payload) {
            if (!isCurrentRequest(context, generation) || payload === null) return;
            if (!isValidPayload(payload)) {
                renderFailure(context, true, focusAfterRetry);
                return;
            }
            renderRecommendation(context, payload, focusAfterRetry);
        }).catch(function () {
            if (isCurrentRequest(context, generation)) {
                renderFailure(context, false, focusAfterRetry);
            }
        }).then(function () {
            if (generation === context.requestGeneration) context.activeRequest = false;
        });
        return true;
    }

    function initWeeklyProgram(root) {
        var scope = root || document;
        var mount = scope.querySelector(MOUNT_SELECTOR);
        if (!mount) return false;
        if (mount.dataset.weeklyProgramInitialized === '1') return false;

        var copyNode = mount.querySelector(COPY_SELECTOR);
        var copy;
        try {
            copy = JSON.parse(copyNode ? copyNode.textContent : '');
        } catch (ignored) {
            return false;
        }
        if (!isPlainObject(copy)) return false;

        mount.dataset.weeklyProgramInitialized = '1';
        mount.dataset.weeklyProgramState = STATES.idle;
        mount.removeAttribute('aria-hidden');
        mount.classList.add('card', 'weekly-program-card');
        var context = {
            mount: mount,
            copy: copy,
            locale: window.LOCALE === 'en' ? 'en' : 'tr',
            activeRequest: false,
            requestGeneration: 0
        };
        loadRecommendation(context, false);
        return true;
    }

    window.FitXWeeklyProgram = {init: initWeeklyProgram};
    initWeeklyProgram(document);
})();
