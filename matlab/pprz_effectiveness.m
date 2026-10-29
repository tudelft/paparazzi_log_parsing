function [r, fig] = pprz_effectiveness(f, varargin)
%PPRZ_EFFECTIVENESS Estimate the control effectiveness of every actuator on the body angular acceleration.
%   r = pprz_effectiveness(f)                        whole flight, plotted
%   r = pprz_effectiveness(f, 'range', [20 80], 'cutoff', 8, 'input', 'rpm', 'delay', 0.02)
%   Fits Δω̇ = G·Δu by least squares on sample-to-sample increments (INDI style), after the same zero-phase
%   low-pass (pprz.lowpass) of the body rates and the inputs.
%   Options:
%     'input'   'auto' (default: ESC rpm per motor when logged, else ACTUATORS), 'rpm' or 'actuators'
%     'range'   [t0 t1] s from takeoff (default the whole flight)
%     'cutoff'  low-pass cut-off [Hz] (default 8)
%     'delay'   input delay [s] (default 0)
%   r has G (3×k, rad/s² per input unit; rows p, q, r), names, fit (R² per axis), t, measured and
%   predicted Δω̇ (N×3), and input ('rpm' or 'actuators').
    opts = struct('input', 'auto', 'range', [0 Inf], 'cutoff', 8, 'delay', 0, 'plot', true);
    for k = 1:2:numel(varargin)
        opts.(lower(varargin{k})) = varargin{k + 1};
    end
    d = f.data;
    [tr, pqr, source] = pprz.rates(d);
    if numel(tr) < 20
        error('pprz_effectiveness:rates', 'No body rates logged (IMU_GYRO or BODY_RATES_ACCEL).');
    end
    use_rpm = strcmp(opts.input, 'rpm') || strcmp(opts.input, 'auto') && pprz.has(d, 'ESC');
    if use_rpm
        if ~pprz.has(d, 'ESC')
            error('pprz_effectiveness:input', 'ESC was not logged.');
        end
        [tu, u, names] = esc_rpm(d.ESC);
    else
        if ~pprz.has(d, 'ACTUATORS')
            error('pprz_effectiveness:input', 'ACTUATORS was not logged.');
        end
        [u, names, tu] = pprz.actuators(d);
    end
    [tu, keep] = unique(tu);
    u = u(keep, :);

    fs = 1 / median(diff(tr));
    t0 = max([opts.range(1), tr(1), tu(1)]);
    t1 = min([opts.range(2), tr(end), tu(end)]);
    t = (t0:1 / fs:t1)';
    if numel(t) < 20
        error('pprz_effectiveness:range', 'Too little overlap of rates and inputs in this range.');
    end
    w = pprz.lowpass(interp1(tr, pqr, t), opts.cutoff, fs);
    uf = pprz.lowpass(interp1(tu, u, t - opts.delay, 'linear', 'extrap'), opts.cutoff, fs);
    wdot = diff(w) * fs;
    dwdot = diff(wdot);
    du = diff(uf(2:end, :));
    used = max(abs(du), [], 1) > 1e-6 * max(abs(du(:)));  % inputs that never move cannot be estimated
    G = zeros(3, numel(names));
    G(:, used) = (pinv(du(:, used)) * dwdot)';  % minimum norm when inputs move together
    predicted = du * G';
    fit = 1 - sum((dwdot - predicted) .^ 2) ./ sum((dwdot - mean(dwdot)) .^ 2);
    r = struct('G', G, 'names', {names}, 'fit', fit, 't', t(3:end), 'measured', dwdot, ...
        'predicted', predicted, 'input', ifelse(use_rpm, 'rpm', 'actuators'), 'rates', source);
    if any(fit < 0.5)
        warning('pprz_effectiveness:fit', ['The fit explains little of the angular acceleration (R² %.2f %.2f %.2f). ' ...
            'Choose a hover range or one with excitation (doublets, chirps).'], fit(1), fit(2), fit(3));
    end
    fig = gobjects(0);
    if opts.plot
        fig = draw(r, f, opts);
    end
end

function [t, u, names] = esc_rpm(m)
    % One column per motor_id, on the times of the first motor.
    ids = unique(m.motor_id);
    first = m.motor_id == ids(1);
    t = m.t(first);
    u = zeros(numel(t), numel(ids));
    names = cell(1, numel(ids));
    for k = 1:numel(ids)
        rows = m.motor_id == ids(k);  % motors that never spin are dropped by the fit
        [tk, keep] = unique(m.t(rows));
        v = double(m.rpm(rows));
        u(:, k) = interp1(tk, v(keep), t, 'linear', 'extrap');
        names{k} = sprintf('rpm %d', ids(k));
    end
end

function fig = draw(r, f, opts)
    fig = figure('Name', sprintf('Control effectiveness (%s) - %s', r.input, f.title), 'NumberTitle', 'off');
    axes_ = {'p', 'q', 'r'};
    for a = 1:3
        ax = subplot(5, 1, a);
        hold(ax, 'on');
        grid(ax, 'on');
        plot(ax, r.t, r.measured(:, a), 'DisplayName', 'measured');
        plot(ax, r.t, r.predicted(:, a), 'DisplayName', 'G·Δu');
        ylabel(ax, sprintf('Δ d%s/dt [rad/s²]', axes_{a}));
        title(ax, sprintf('%s axis · R² %.2f', axes_{a}, r.fit(a)), 'FontWeight', 'normal');
        if a == 1
            legend(ax, 'Location', 'best');
        end
    end
    xlabel(ax, 'Time [s]');
    ax = subplot(5, 1, [4 5]);
    bar(ax, r.G');
    grid(ax, 'on');
    set(ax, 'XTick', 1:numel(r.names), 'XTickLabel', r.names, 'TickLabelInterpreter', 'none');
    ylabel(ax, 'G [rad/s² per unit]');
    legend(ax, axes_, 'Location', 'best');
    title(ax, sprintf('Effectiveness, low-pass %g Hz, delay %g s', opts.cutoff, opts.delay), ...
        'FontWeight', 'normal');
end

function v = ifelse(c, a, b)
    if c
        v = a;
    else
        v = b;
    end
end
