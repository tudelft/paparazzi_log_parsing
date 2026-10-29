function [ex, figs] = pprz_sysid(f, varargin)
%PPRZ_SYSID Find identification experiments in a flight, measure the responses and plot them.
%   ex = pprz_sysid(f)                  every kind found; prints a table and makes the figures
%   ex = pprz_sysid(f, 'kinds', {'chirp'}, 'plot', false)
%   Kinds:
%     'doublet'  DOUBLET active (sys_id module): delay and effectiveness of the input on the body rate
%     'chirp'    CHIRP active: the same, plus the frequency response (gain, phase, coherence)
%     'setting'  setting changes in flight; step metrics of 'signal' when given (e.g. 'ROTORCRAFT_FP.phi')
%     'rc'       RC stick steps (roll, pitch, yaw): step metrics of the roll/pitch angle or yaw rate
%   Options:
%     'axes'    body axis (1 p, 2 q, 3 r, 0 none) of each sys_id axis 0, 1, ...; default [1 2 3 0]
%               (the module's default roll, pitch, yaw, thrust commands)
%     'signal'  'MSG.field' the setting steps act on
%     'window'  seconds after a step to judge it (default 4)
%   ex is a struct array with kind, label, t0, t1, axis and metrics: delay [s], rise [s], overshoot [%],
%   settling [s] for steps; delay [s], effectiveness [rad/s² per unit] and fit (R²) for doublets and
%   chirps; a chirp also has fr (pprz.freq_response). Body rates come from pprz.rates. Control
%   effectiveness of all actuators at once: pprz_effectiveness.
    opts = struct('kinds', {{'doublet', 'chirp', 'setting', 'rc'}}, 'axes', [1 2 3 0], 'signal', '', ...
        'window', 4, 'plot', true);
    for k = 1:2:numel(varargin)
        opts.(lower(varargin{k})) = varargin{k + 1};
    end
    d = f.data;
    [tr, pqr] = pprz.rates(d);
    grid_ = struct('t', zeros(0, 1), 'w', zeros(0, 3), 'wdot', zeros(0, 3), 'fs', NaN);
    if numel(tr) > 10
        fs = 1 / median(diff(tr));
        grid_.t = (tr(1):1 / fs:tr(end))';
        grid_.w = interp1(tr, pqr, grid_.t);
        grid_.wdot = gradient_rows(pprz.lowpass(grid_.w, min(20, fs / 5), fs), 1 / fs);
        grid_.fs = fs;
    end
    ex = struct('kind', {}, 'label', {}, 't0', {}, 't1', {}, 'axis', {}, 'metrics', {}, 'fr', {}, ...
        'plot', {});
    for msg = {'doublet', 'chirp'}
        if any(strcmp(opts.kinds, msg{1})) && pprz.has(d, upper(msg{1}))
            ex = [ex, excitations(d.(upper(msg{1})), msg{1}, grid_, opts)]; %#ok<AGROW>
        end
    end
    if any(strcmp(opts.kinds, 'setting'))
        ex = [ex, setting_steps(f, opts)];
    end
    if any(strcmp(opts.kinds, 'rc')) && pprz.has(d, 'ROTORCRAFT_RADIO_CONTROL')
        ex = [ex, rc_steps(d, grid_, opts)];
    end
    figs = gobjects(0);
    if opts.plot && ~isempty(ex)
        figs = draw(ex, f);
    end
    if nargout == 0 || opts.plot
        print_table(ex);
    end
    ex = rmfield(ex, 'plot');
    if nargout == 0
        clear ex
    end
end

function ex = excitations(m, kind, g, opts)
    % One experiment per run of active = 1, with the input on the rate grid.
    ex = struct('kind', {}, 'label', {}, 't0', {}, 't1', {}, 'axis', {}, 'metrics', {}, 'fr', {}, 'plot', {});
    on = double(m.active(:)) > 0;
    starts = find(on & [true; ~on(1:end - 1)]);
    stops = find(on & [~on(2:end); true]);
    for k = 1:numel(starts)
        rows = starts(k):stops(k);
        a = double(m.axis(starts(k)));
        ax = 0;
        if a + 1 <= numel(opts.axes)
            ax = opts.axes(a + 1);
        end
        t0 = m.t(starts(k));
        t1 = m.t(stops(k));
        e = struct('kind', kind, 'label', sprintf('%s axis %d', kind, a), 't0', t0, 't1', t1, 'axis', ax, ...
            'metrics', struct(), 'fr', [], 'plot', []);
        span = [t0 - 1, t1 + 2];
        if ax > 0 && ~isempty(g.t)
            in = g.t >= span(1) & g.t <= span(2);
            u = hold_input(m.t, double(m.current_value), g.t(in), rows);
            e.metrics = rate_fit(u, g.wdot(in, ax), g.fs);
            if strcmp(kind, 'chirp') && t1 - t0 > 2
                act = g.t >= t0 & g.t <= t1;
                n = 2 ^ nextpow2(max(64, g.fs * (t1 - t0) / 4));
                e.fr = pprz.freq_response(hold_input(m.t, double(m.current_value), g.t(act), rows), ...
                    g.w(act, ax), g.fs, n);
                e.fr.band = [double(m.fstart(starts(k))), double(m.fstop(starts(k)))];
            end
            e.plot = struct('t', g.t(in), 'u', u, 'y', rad2deg(g.w(in, ax)), 'ylabel', ...
                sprintf('%s [deg/s]', rate_name(ax)), 'step', NaN);
        end
        ex(end + 1) = e; %#ok<AGROW>
    end
end

function u = hold_input(t, v, tq, rows)
    % The excitation value held between samples; zero outside the active run.
    u = zeros(size(tq));
    if numel(rows) > 1
        u = interp1(t(rows), v(rows), tq, 'previous', 0);
    end
end

function m = rate_fit(u, wdot, fs)
    % Angular acceleration ≈ G · input delayed by the lag that fits best (up to 0.3 s).
    m = struct('delay', NaN, 'effectiveness', NaN, 'fit', NaN);
    if all(u == u(1))
        return
    end
    best = -Inf;
    for lag = 0:round(0.3 * fs)
        x = u(1:end - lag);
        y = wdot(1 + lag:end);
        g = x \ y;
        r2 = 1 - sum((y - g * x) .^ 2) / sum((y - mean(y)) .^ 2);
        if r2 > best
            best = r2;
            m = struct('delay', lag / fs, 'effectiveness', g, 'fit', r2);
        end
    end
end

function ex = setting_steps(f, opts)
    ex = struct('kind', {}, 'label', {}, 't0', {}, 't1', {}, 'axis', {}, 'metrics', {}, 'fr', {}, 'plot', {});
    if ~isfield(f, 'aircraft') || ~isfield(f.aircraft, 'settings') || ~isfield(f.aircraft.settings, 'changes')
        return
    end
    ch = f.aircraft.settings.changes;
    names = cellstr(ch.name);
    offset = f.time.offset_s;
    if ~isfinite(offset)
        offset = 0;
    end
    y = [];
    if ~isempty(opts.signal)
        parts = strsplit(opts.signal, '.');
        if pprz.has(f.data, parts{1}) && isfield(f.data.(parts{1}), parts{2})
            y = struct('t', f.data.(parts{1}).t, 'v', double(f.data.(parts{1}).(parts{2})));
        else
            warning('pprz_sysid:signal', '%s was not logged.', opts.signal);
        end
    end
    inflight = true(size(ch.t));
    if isfield(ch, 'in_flight')
        inflight = logical(ch.in_flight);
    end
    for k = find(inflight(:))'
        t0 = ch.t(k) - offset;
        t1 = t0 + opts.window;
        later = find(strcmp(names, names{k}) & ch.t(:) - offset > t0, 1);
        if ~isempty(later)
            t1 = min(t1, ch.t(later) - offset);
        end
        e = struct('kind', 'setting', 'label', sprintf('%s %g → %g', names{k}, ch.from(k), ch.to(k)), ...
            't0', t0, 't1', t1, 'axis', 0, 'metrics', struct(), 'fr', [], 'plot', []);
        if ~isempty(y)
            e.metrics = pprz.step_info(y.t, y.v, t0, t1);
            in = y.t >= t0 - 1 & y.t <= t1;
            u = ch.from(k) + (ch.to(k) - ch.from(k)) * (y.t(in) >= t0);
            e.plot = struct('t', y.t(in), 'u', u, 'y', y.v(in), 'ylabel', strrep(opts.signal, '_', '\_'), ...
                'step', t0);
        end
        ex(end + 1) = e; %#ok<AGROW>
    end
end

function ex = rc_steps(d, g, opts)
    % Quick stick moves (over 30 % of full deflection within 0.25 s) that are then held for 0.5 s.
    ex = struct('kind', {}, 'label', {}, 't0', {}, 't1', {}, 'axis', {}, 'metrics', {}, 'fr', {}, 'plot', {});
    rc = d.ROTORCRAFT_RADIO_CONTROL;
    sticks = {'roll', 'pitch', 'yaw'};
    angle = {'phi', 'theta', ''};
    for s = 1:3
        if ~isfield(rc, sticks{s})
            continue
        end
        t = rc.t(:);
        v = double(rc.(sticks{s})(:));
        [y, label] = response(d, angle{s}, g, s);
        if isempty(y)
            continue
        end
        last = -Inf;
        for i = 1:numel(t)
            if t(i) < last + 1.5
                continue
            end
            before = t >= t(i) - 0.5 & t <= t(i);
            jump = t > t(i) & t <= t(i) + 0.25;
            held = t >= t(i) + 0.3 & t <= t(i) + 0.8;
            if nnz(before) < 2 || ~any(jump) || nnz(held) < 2
                continue
            end
            v0 = mean(v(before));
            v1 = mean(v(held));
            if abs(v1 - v0) < 0.3 * 9600 || std(v(before)) > 400 || std(v(held)) > 400 || ...
                    abs(v(find(jump, 1, 'last')) - v0) < 0.27 * 9600
                continue
            end
            moved = find(t > t(i) & abs(v - v0) > 0.05 * 9600, 1);
            t0 = t(moved - 1);  % the last sample before the stick left its position
            t1 = t0 + opts.window;
            off = find(t > t0 + 0.3 & abs(v - v1) >= 0.1 * 9600, 1);  % the stick moves again
            if ~isempty(off)
                t1 = min(t1, t(off - 1));
            end
            e = struct('kind', 'rc', 'label', sprintf('RC %s %+.0f %%', sticks{s}, (v1 - v0) / 96), ...
                't0', t0, 't1', t1, 'axis', s, 'metrics', pprz.step_info(y.t, y.v, t0, t1), 'fr', [], ...
                'plot', []);
            in = y.t >= t0 - 1 & y.t <= t1;
            e.plot = struct('t', y.t(in), 'u', interp1(t, v, y.t(in), 'previous', v0), 'y', y.v(in), ...
                'ylabel', label, 'step', t0);
            ex(end + 1) = e; %#ok<AGROW>
            last = t1;
        end
    end
end

function [y, label] = response(d, field, g, s)
    % Roll and pitch sticks command an angle, the yaw stick a rate.
    y = [];
    label = '';
    if ~isempty(field) && pprz.has(d, 'ROTORCRAFT_FP') && isfield(d.ROTORCRAFT_FP, field)
        y = struct('t', d.ROTORCRAFT_FP.t, 'v', double(d.ROTORCRAFT_FP.(field)));
        label = sprintf('%s [deg]', field);
    elseif ~isempty(g.t)
        y = struct('t', g.t, 'v', rad2deg(g.w(:, s)));
        label = sprintf('%s [deg/s]', rate_name(s));
    end
end

function figs = draw(ex, f)
    figs = gobjects(0);
    kinds = unique({ex.kind}, 'stable');
    for k = 1:numel(kinds)
        e = ex(strcmp({ex.kind}, kinds{k}) & ~cellfun(@isempty, {ex.plot}));
        if isempty(e)
            continue
        end
        for first = 1:12:numel(e)
            part = e(first:min(first + 11, numel(e)));
            figs(end + 1) = figure('Name', sprintf('System ID: %s - %s', kinds{k}, f.title), ...
                'NumberTitle', 'off'); %#ok<AGROW>
            cols = ceil(sqrt(numel(part)));
            rows = ceil(numel(part) / cols);
            for i = 1:numel(part)
                ax = subplot(rows, cols, i);
                draw_one(ax, part(i));
            end
        end
    end
    chirps = ex(strcmp({ex.kind}, 'chirp') & ~cellfun(@isempty, {ex.fr}));
    if ~isempty(chirps)
        figs(end + 1) = bode(chirps, f);
    end
end

function draw_one(ax, e)
    % The response with the input scaled onto it (dashed), the step and the metrics in the title.
    p = e.plot;
    hold(ax, 'on');
    grid(ax, 'on');
    plot(ax, p.t, p.y, 'DisplayName', strrep(p.ylabel, '\_', '_'));
    span = [min(p.y), max(p.y)];
    if diff(span) > 0 && max(p.u) > min(p.u)
        scaled = span(1) + (p.u - min(p.u)) / (max(p.u) - min(p.u)) * diff(span);
        plot(ax, p.t, scaled, '--', 'Color', [0.5 0.5 0.5], 'DisplayName', 'input (scaled)');
    end
    if isfinite(p.step)
        line(ax, [p.step p.step], get(ax, 'YLim'), 'Color', [0.8 0.3 0.3], 'LineStyle', ':', ...
            'HandleVisibility', 'off');
    end
    ylabel(ax, p.ylabel);
    xlabel(ax, 'Time [s]');
    title(ax, {strrep(e.label, '_', '\_'), metrics_text(e.metrics)}, 'FontWeight', 'normal', 'FontSize', 8);
end

function text = metrics_text(m)
    if isfield(m, 'rise')
        text = sprintf('delay %.2f s · rise %.2f s · overshoot %.0f %% · settle %.2f s', m.delay, m.rise, ...
            m.overshoot, m.settling);
    elseif isfield(m, 'effectiveness')
        text = sprintf('delay %.3f s · G %.3g · R² %.2f', m.delay, m.effectiveness, m.fit);
    else
        text = '';
    end
end

function fig = bode(chirps, f)
    fig = figure('Name', sprintf('System ID: chirp frequency response - %s', f.title), 'NumberTitle', 'off');
    axs = [subplot(3, 1, 1), subplot(3, 1, 2), subplot(3, 1, 3)];
    for a = axs
        hold(a, 'on');
        grid(a, 'on');
        set(a, 'XScale', 'log');
    end
    for k = 1:numel(chirps)
        r = chirps(k).fr;
        band = r.f >= r.band(1) & r.f <= r.band(2) & r.f > 0;
        if ~any(band)
            band = r.f > 0;
        end
        name = sprintf('%s (%.0f s)', chirps(k).label, chirps(k).t0);
        semilogx(axs(1), r.f(band), r.gain_db(band), 'DisplayName', name);
        semilogx(axs(2), r.f(band), r.phase_deg(band), 'DisplayName', name);
        semilogx(axs(3), r.f(band), r.coherence(band), 'DisplayName', name);
    end
    ylabel(axs(1), 'Gain [dB]');
    ylabel(axs(2), 'Phase [deg]');
    ylabel(axs(3), 'Coherence');
    set(axs(3), 'YLim', [0 1]);
    xlabel(axs(3), 'Frequency [Hz]');
    title(axs(1), 'Body rate [rad/s] per input unit');
    legend(axs(1), 'Location', 'best', 'Interpreter', 'none');
    linkaxes(axs, 'x');
end

function print_table(ex)
    if isempty(ex)
        disp('No identification experiments found.');
        return
    end
    fprintf('%-8s %8s %8s  %-36s %s\n', 'kind', 't0 [s]', 't1 [s]', 'experiment', 'result');
    for k = 1:numel(ex)
        fprintf('%-8s %8.1f %8.1f  %-36s %s\n', ex(k).kind, ex(k).t0, ex(k).t1, ex(k).label, ...
            metrics_text(ex(k).metrics));
    end
end

function name = rate_name(axis)
    names = 'pqr';
    name = names(axis);
end

function dy = gradient_rows(y, dt)
    dy = zeros(size(y));
    if size(y, 1) > 2
        dy(2:end - 1, :) = (y(3:end, :) - y(1:end - 2, :)) / (2 * dt);
        dy(1, :) = (y(2, :) - y(1, :)) / dt;
        dy(end, :) = (y(end, :) - y(end - 1, :)) / dt;
    end
end
